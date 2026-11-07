"""REST API endpoints for transitive dependencies, AST refactoring, and auto-patching."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB
from vibetrace.refactor.models import CandidateSplitBlock
from vibetrace.security.auto_patcher import DependencyAutoPatcher
from vibetrace.security.dep_graph import (
    DependencyGraphBuilder,
    normalize_package_name,
)
from vibetrace.security.pr_bot import PRTaintBot
from vibetrace.security.service import SecurityService
from vibetrace.services.diagnose_service import DiagnoseService
from vibetrace.services.refactor_service import RefactorService


def _get_project_root() -> Path:
    env_root = os.environ.get("VIBETRACE_PROJECT_ROOT")
    if env_root and Path(env_root).is_dir():
        return Path(env_root).resolve()
    return Path.cwd().resolve()


class RefactorSplitRequest(BaseModel):
    file_path: str = Field(..., min_length=1)
    symbol_name: str = Field(..., min_length=1)
    start_line: int | None = None
    end_line: int | None = None
    helper_name: str | None = None
    apply: bool = False


class PatchDepsRequest(BaseModel):
    package: str | None = None
    apply: bool = False
    suppress_unpatched: bool = False


def get_engineering_router(get_db: Callable[[], IndexerDB]) -> APIRouter:
    router = APIRouter(tags=["engineering"])

    @router.get("/api/deps/why")
    def get_deps_why(package: str = Query(..., min_length=1)) -> dict[str, Any]:
        """Explain root causation chains that introduce a transitive dependency."""
        root = _get_project_root()
        graph = DependencyGraphBuilder.build(root)
        norm_target = normalize_package_name(package)
        chains = graph.resolve_why(norm_target)
        return {
            "target": package,
            "normalized": norm_target,
            "found": len(chains) > 0,
            "chains_count": len(chains),
            "chains": [c.model_dump() for c in chains],
        }

    @router.get("/api/deps/tree")
    def get_deps_tree(
        package: str | None = Query(default=None),
        max_depth: int = Query(default=3, ge=1, le=10),
    ) -> dict[str, Any]:
        """Render the lockfile dependency tree from root manifests down to leaves."""
        root = _get_project_root()
        graph = DependencyGraphBuilder.build(root)
        ascii_tree = graph.render_tree(root_package=package, max_depth=max_depth)
        return {
            "root_package": package,
            "max_depth": max_depth,
            "tree": ascii_tree,
        }

    @router.get("/api/deps/conflicts")
    def get_deps_conflicts() -> dict[str, Any]:
        """Detect diamond dependency conflicts across lockfile versions."""
        root = _get_project_root()
        graph = DependencyGraphBuilder.build(root)
        conflicts = graph.detect_conflicts()
        return {
            "total_conflicts": len(conflicts),
            "conflicts": [c.model_dump() for c in conflicts],
        }

    @router.get("/api/refactor/candidates", response_model=list[CandidateSplitBlock])
    def get_refactor_candidates(
        file_path: str = Query(..., min_length=1),
        symbol_name: str = Query(..., min_length=1),
    ) -> list[CandidateSplitBlock]:
        """Suggest candidate contiguous AST blocks suitable for method extraction."""
        root = _get_project_root()
        target_p = (root / file_path).resolve()
        if not target_p.is_relative_to(root.resolve()):
            raise HTTPException(status_code=400, detail="file_path escapes project root boundary")
        try:
            return RefactorService.suggest_split_candidates(root, file_path, symbol_name)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post("/api/refactor/split")
    def post_refactor_split(body: RefactorSplitRequest) -> dict[str, Any]:
        """Construct or execute an AST-based helper method extraction refactoring."""
        root = _get_project_root()
        target_p = (root / body.file_path).resolve()
        if not target_p.is_relative_to(root.resolve()):
            raise HTTPException(status_code=400, detail="file_path escapes project root boundary")
        try:
            plan = RefactorService.build_refactor_plan(
                project_root=root,
                file_path=body.file_path,
                func_name=body.symbol_name,
                start_line=body.start_line,
                end_line=body.end_line,
                helper_name=body.helper_name,
            )
            if body.apply:
                if not plan.can_auto_apply:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot auto-apply refactor: {plan.rejection_reason}",
                    )
                RefactorService.apply_refactor_plan(root, plan)
            return plan.to_dict()
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    @router.post("/api/security/patch-deps")
    def post_patch_deps(body: PatchDepsRequest) -> dict[str, Any]:
        """Build and optionally apply dependency vulnerability patch plan."""
        root = _get_project_root()
        db = get_db()
        report_sec = SecurityService.audit_project(
            db=db,
            project_root=root,
            check_dependencies=True,
        )
        patch_plan = DependencyAutoPatcher.build_patch_plan(
            project_root=root,
            findings=report_sec.findings,
            db=db,
            package_filter=body.package,
        )
        if body.apply or body.suppress_unpatched:
            patch_plan = DependencyAutoPatcher.apply_patch_plan(
                project_root=root,
                report=patch_plan,
                suppress_unpatched=body.suppress_unpatched,
            )
        return patch_plan.to_dict()

    @router.get("/api/security/pr-taint")
    def get_pr_taint(
        base_ref: str = Query(default="main"),
        staged: bool = Query(default=False),
        pr_number: int | None = Query(default=None),
        apply: bool = Query(default=False),
    ) -> dict[str, Any]:
        """Generate PR-scoped taint review report with inline suggestions."""
        root = _get_project_root()
        db = get_db()
        report = PRTaintBot.analyze_pr_diff(
            project_root=root,
            db=db,
            base_ref=base_ref,
            staged=staged,
            pr_number=pr_number,
        )
        if apply and report.comments:
            PRTaintBot.apply_remediations(root, report)
        return report.model_dump()

    @router.get("/api/diagnose")
    def get_diagnose_symbol(
        symbol: str = Query(..., min_length=1),
        depth: int = Query(default=2, ge=1, le=5),
        include_code: bool = Query(default=True),
    ) -> dict[str, Any]:
        """Consolidated 360° symbol diagnosis (slice, callers, blast radius, specs, tests)."""
        root = _get_project_root()
        db = get_db()
        report = DiagnoseService.diagnose_symbol(
            db=db,
            project_root=root,
            symbol_name=symbol,
            max_depth=depth,
            include_code=include_code,
        )
        if not report.found:
            raise HTTPException(status_code=404, detail=report.safety_advisory)
        return report.model_dump()

    return router
