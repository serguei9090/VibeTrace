"""FastMCP tools for transitive dependencies, AST refactoring, auto-patching, and docs audit."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from vibetrace.docs.symbol_linker import SymbolDocLinker
from vibetrace.indexer.db import IndexerDB
from vibetrace.mcp.schemas import (
    CheckDocsDriftInput,
    DiagnoseSymbolInput,
    ExplainDependencyInput,
    PatchDependencyInput,
    RefactorMethodInput,
    ResponseFormat,
)
from vibetrace.security.auto_patcher import DependencyAutoPatcher
from vibetrace.security.dep_graph import (
    DependencyGraphBuilder,
    normalize_package_name,
)
from vibetrace.security.service import SecurityService
from vibetrace.services.diagnose_service import (
    DiagnoseService,
    format_diagnosis_md,
)
from vibetrace.services.refactor_service import RefactorService

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP

VIBETRACE_DIR = ".vibetrace"
INDEX_DB_NAME = "vibetrace.db"
INDEX_NOT_FOUND_MSG = "Error: VibeTrace index not found. Run index first."

_ROOT_MARKERS = (
    ".vibetrace",
    "vibetrace_context",
    ".vibetrace.toml",
    "vibetrace.toml",
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
)


def _resolve_project_root(scope_path: str) -> Path:
    target = Path(scope_path).resolve() if scope_path else Path.cwd().resolve()
    for p in [target, *list(target.parents)]:
        if any((p / marker).exists() for marker in _ROOT_MARKERS):
            return p
    return target


def vibetrace_explain_dependency(params: ExplainDependencyInput) -> str:
    """Explain why a package is installed via causation chains, trees, or diamond conflicts."""
    try:
        root = _resolve_project_root(params.scope_path)
        graph = DependencyGraphBuilder.build(root)

        if params.action == "conflicts":
            conflicts = graph.detect_conflicts()
            if params.response_format == ResponseFormat.JSON:
                return json.dumps(
                    {
                        "total_conflicts": len(conflicts),
                        "conflicts": [c.model_dump() for c in conflicts],
                    },
                    indent=2,
                )
            if not conflicts:
                return "✅ Zero diamond dependency conflicts detected across project lockfiles."
            lines = [f"⚠️ Found {len(conflicts)} diamond dependency conflict(s):\n"]
            for c in conflicts:
                lines.append(f"### `{c.package_name}` (ecosystem: {c.ecosystem})")
                for v, parents in c.parents_by_version.items():
                    lines.append(f"- **Version `{v}`** required by: {', '.join(parents)}")
                lines.append("")
            return "\n".join(lines)

        if params.action == "tree":
            ascii_tree = graph.render_tree(
                root_package=params.package_name or None,
                max_depth=params.max_depth,
            )
            if params.response_format == ResponseFormat.JSON:
                return json.dumps(
                    {
                        "root_package": params.package_name or "all",
                        "max_depth": params.max_depth,
                        "tree": ascii_tree,
                    },
                    indent=2,
                )
            header = f"# Transitive Dependency Tree (max depth: {params.max_depth})\n"
            return header + f"```text\n{ascii_tree}\n```"

        # Default action: "why"
        if not params.package_name:
            return "Error: 'package_name' parameter is required for action='why'."

        norm_target = normalize_package_name(params.package_name)
        chains = graph.resolve_why(norm_target)
        if params.response_format == ResponseFormat.JSON:
            return json.dumps(
                {
                    "target": params.package_name,
                    "normalized": norm_target,
                    "found": len(chains) > 0,
                    "chains_count": len(chains),
                    "chains": [c.model_dump() for c in chains],
                },
                indent=2,
            )
        if not chains:
            return f"❌ Package '{params.package_name}' was not found in lockfiles or manifests."

        return graph.render_ascii_chains(params.package_name, chains)
    except Exception as e:
        return f"Error analyzing transitive dependencies: {e}"


def vibetrace_refactor_method(params: RefactorMethodInput) -> str:
    """Analyze AST variable lifetimes and execute surgical helper method extraction."""
    try:
        root = _resolve_project_root(params.scope_path)

        # 1. Suggest split candidates if line range is not specified
        if params.start_line is None or params.end_line is None:
            candidates = RefactorService.suggest_split_candidates(
                project_root=root,
                file_path=params.file_path,
                func_name=params.symbol_name,
            )
            if params.response_format == ResponseFormat.JSON:
                return json.dumps([c.to_dict() for c in candidates], indent=2)
            if not candidates:
                return f"[INFO] No candidate split blocks found for `{params.symbol_name}`."
            lines = [
                f"# Candidate Split Blocks for `{params.symbol_name}` in `{params.file_path}`\n"
            ]
            for c in candidates:
                lines.append(
                    f"- **Lines {c.start_line}..{c.end_line}**: {c.summary} "
                    f"(Complexity: {c.complexity_score}, Reduction: -{c.complexity_reduction})\n"
                    f"  - Inputs: `{', '.join(c.inputs) or 'none'}`\n"
                    f"  - Outputs: `{', '.join(c.outputs) or 'none'}`"
                )
            return "\n".join(lines)

        # 2. Build refactoring plan
        plan = RefactorService.build_refactor_plan(
            project_root=root,
            file_path=params.file_path,
            func_name=params.symbol_name,
            start_line=params.start_line,
            end_line=params.end_line,
            helper_name=params.helper_name,
        )

        if params.apply:
            if not plan.can_auto_apply:
                return (
                    f"❌ Cannot apply refactor automatically: {plan.rejection_reason}\n\n"
                    "Suggested Helper Code:\n```python\n"
                    f"{plan.helper_code}\n```"
                )
            RefactorService.apply_refactor_plan(root, plan)

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(plan.to_dict(), indent=2)

        status_msg = (
            "✅ Refactoring applied successfully to disk."
            if params.apply
            else "[PREVIEW] Refactoring plan preview (dry-run):"
        )
        return (
            f"# AST Method Extraction Plan for `{params.symbol_name}`\n"
            f"{status_msg}\n\n"
            f"- **Target:** `{params.file_path}:{params.start_line}..{params.end_line}`\n"
            f"- **Extracted Helper:** `{plan.helper_name}`\n"
            f"- **Inputs / Args:** `{', '.join(plan.inputs) or 'none'}`\n"
            f"- **Outputs / Returns:** `{', '.join(plan.outputs) or 'none'}`\n\n"
            f"### Extracted Helper Definition\n```python\n{plan.helper_code}\n```\n\n"
            f"### Replacement Call Site\n```python\n{plan.call_replacement}\n```"
        )
    except Exception as e:
        return f"Error executing AST refactoring: {e}"


def vibetrace_patch_dependency(params: PatchDependencyInput) -> str:
    """Build and apply dependency vulnerability upgrade plans or suppress unpatched CVEs."""
    try:
        root = _resolve_project_root(params.scope_path)
        db_path = root / VIBETRACE_DIR / INDEX_DB_NAME
        if not db_path.is_file():
            return INDEX_NOT_FOUND_MSG
        db = IndexerDB(db_path)

        report_sec = SecurityService.audit_project(
            db=db,
            project_root=root,
            check_dependencies=True,
        )
        plan = DependencyAutoPatcher.build_patch_plan(
            project_root=root,
            findings=report_sec.findings,
            db=db,
            package_filter=params.package_name,
        )

        if params.apply or params.suppress_unpatched:
            plan = DependencyAutoPatcher.apply_patch_plan(
                project_root=root,
                report=plan,
                suppress_unpatched=params.suppress_unpatched,
            )

        if params.response_format == ResponseFormat.JSON:
            return json.dumps(plan.to_dict(), indent=2)

        lines = [
            f"# Dependency Auto-Patcher Report ({'Applied' if params.apply else 'Dry Run'})\n",
            f"- **Total Vulnerable Packages:** {plan.total_vulnerable_packages}",
            f"- **Reachable (Active Risk):** {plan.unpatched_reachable_count}",
            f"- **Unreachable (Dormant):** {plan.unpatched_unreachable_count}",
            f"- **Ready to Upgrade:** {plan.upgradable_count}\n",
        ]
        if plan.actions:
            lines.append("### Planned Actions:")
            for act in plan.actions:
                reach_tag = "🔴 REACHABLE" if act.reachability_callers > 0 else "⚪ Dormant"
                target_v = act.fixed_version or "N/A"
                lines.append(
                    f"- `{act.package_name}`: {act.current_version} ➔ `{target_v}` "
                    f"[{reach_tag}] via `{act.ecosystem}`"
                )
                if act.proposed_command:
                    lines.append(f"  Command: `{act.proposed_command}`")
        else:
            lines.append("✅ No vulnerable packages require upgrading.")
        return "\n".join(lines)
    except Exception as e:
        return f"Error patching dependencies: {e}"


def vibetrace_check_docs_drift(params: CheckDocsDriftInput) -> str:
    """Audit project documentation for dead codebase symbol references and sync live links."""
    try:
        root = _resolve_project_root(params.scope_path)
        db_path = root / VIBETRACE_DIR / INDEX_DB_NAME
        if not db_path.is_file():
            return INDEX_NOT_FOUND_MSG
        db = IndexerDB(db_path)

        synced_count = 0
        if params.sync:
            synced_count = SymbolDocLinker.sync_links(root, db)

        report = SymbolDocLinker.audit_doc_links(root, db)
        if params.response_format == ResponseFormat.JSON:
            data = report.model_dump()
            data["synced_links"] = synced_count
            return json.dumps(data, indent=2)

        lines = [
            "# Documentation Symbol Reference Audit\n",
            f"- **Total Symbols Referenced in Docs:** {report.total_symbols_referenced}",
            f"- **Valid Symbol References:** {report.valid_links_count}",
            f"- **Drift Findings (Dead/Broken):** {report.broken_links_count}",
        ]
        if params.sync:
            lines.append(f"- **Synced Bi-Directional Links:** {synced_count}")

        if report.findings:
            lines.append("\n### Broken / Dead Symbol Findings in Docs:")
            for f in report.findings:
                lines.append(f"- `{f.raw_symbol}` in `{f.doc_path}:{f.line_number}`: {f.issue}")
        else:
            lines.append("\n✅ All documented symbols match live AST definitions in the codebase!")
        return "\n".join(lines)
    except Exception as e:
        return f"Error checking documentation drift: {e}"


def vibetrace_diagnose_symbol(params: DiagnoseSymbolInput) -> str:
    """360° symbol diagnostic: slice, callers with call lines, blast radius, specs, and tests."""
    try:
        root = _resolve_project_root(params.scope_path)
        db_path = root / VIBETRACE_DIR / INDEX_DB_NAME
        if not db_path.is_file():
            return INDEX_NOT_FOUND_MSG
        db = IndexerDB(db_path)

        report = DiagnoseService.diagnose_symbol(
            db=db,
            project_root=root,
            symbol_name=params.symbol_name,
            max_depth=params.max_depth,
            include_code=params.include_code,
        )

        if params.response_format == ResponseFormat.JSON:
            return report.model_dump_json(indent=2)

        return format_diagnosis_md(report)
    except Exception as e:
        return f"Error diagnosing symbol '{params.symbol_name}': {e}"


def register_engineering_tools(mcp: FastMCP) -> None:
    """Register engineering, refactoring, and dependency tools onto the FastMCP server."""
    mcp.tool()(vibetrace_explain_dependency)
    mcp.tool()(vibetrace_refactor_method)
    mcp.tool()(vibetrace_patch_dependency)
    mcp.tool()(vibetrace_check_docs_drift)
    mcp.tool()(vibetrace_diagnose_symbol)
