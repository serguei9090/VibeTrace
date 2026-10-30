"""Service for detecting, hashing, and triaging architectural execution splits."""

from __future__ import annotations

import hashlib
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB


class BranchDetail(BaseModel):
    """Detailed target of an outgoing execution branch."""

    target_name: str
    target_file: str
    relation_type: str
    call_line: int | None = None


class ArchitecturalSplit(BaseModel):
    """Represents a cross-module execution bifurcation in the codebase."""

    node_id: str
    symbol: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    code_hash: str
    branches_count: int
    branches: list[BranchDetail] = Field(default_factory=list)
    status: str = "NEW_UNAPPROVED"  # APPROVED | MODIFIED_CODE | NEW_UNAPPROVED | RISK
    baseline_hash: str | None = None
    rationale: str | None = None
    reviewed_by: str | None = None
    reviewed_at: str | None = None


class SplitAuditReport(BaseModel):
    """Comprehensive summary of split audit results across the project."""

    total_splits: int
    approved_count: int
    modified_count: int
    new_unapproved_count: int
    splits: list[ArchitecturalSplit] = Field(default_factory=list)


def compute_symbol_code_hash(
    file_path: str,
    start_line: int,
    end_line: int,
    project_root: Path,
) -> str:
    """Computes a deterministic SHA-256 hash of a function's normalized source lines."""
    clean_path = file_path.replace("\\", "/").lstrip("/")
    abs_file = (project_root / clean_path).resolve()
    if not abs_file.is_file():
        return hashlib.sha256(b"").hexdigest()

    try:
        lines = abs_file.read_text(encoding="utf-8", errors="replace").splitlines()
        s = max(1, start_line)
        e = min(len(lines), end_line)
        if s > e:
            return hashlib.sha256(b"").hexdigest()

        # Normalize lines: strip whitespace and ignore pure empty lines
        normalized = [ln.strip() for ln in lines[s - 1 : e] if ln.strip()]
        payload = "\n".join(normalized).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()
    except Exception:
        return hashlib.sha256(b"").hexdigest()


def get_splits_toml_path(project_root: Path) -> Path:
    """Returns path to .vibetrace/splits.toml baseline file."""
    vt_dir = project_root / ".vibetrace"
    vt_dir.mkdir(parents=True, exist_ok=True)
    return vt_dir / "splits.toml"


def load_split_baseline(project_root: Path) -> dict[str, dict[str, Any]]:
    """Loads approved splits baseline from .vibetrace/splits.toml.

    Indexed by symbol and qualified_name.
    """
    path = get_splits_toml_path(project_root)
    if not path.is_file():
        return {}

    try:
        content = path.read_text(encoding="utf-8")
        data = tomllib.loads(content)
        splits_list = data.get("splits", [])
        baseline: dict[str, dict[str, Any]] = {}
        for item in splits_list:
            sym = item.get("symbol")
            if sym:
                baseline[sym] = item
                if item.get("qualified_name"):
                    baseline[item["qualified_name"]] = item
        return baseline
    except Exception:
        return {}


def save_split_baseline(project_root: Path, splits: list[dict[str, Any]]) -> Path:
    """Writes updated splits baseline to .vibetrace/splits.toml in valid TOML format."""
    path = get_splits_toml_path(project_root)
    now_iso = datetime.now(UTC).isoformat()

    lines = [
        "# VibeTrace Architectural Splits Baseline",
        "# Auto-generated & version-controlled in Git",
        "",
        "[meta]",
        'version = "1.0.0"',
        f'updated_at = "{now_iso}"',
        f"total_records = {len(splits)}",
        "",
    ]

    for item in splits:
        lines.append("[[splits]]")
        lines.append(f'symbol = "{item.get("symbol", "")}"')
        lines.append(f'qualified_name = "{item.get("qualified_name", "")}"')
        lines.append(f'file_path = "{item.get("file_path", "")}"')
        lines.append(f'status = "{item.get("status", "approved")}"')
        lines.append(f'code_hash = "{item.get("code_hash", "")}"')
        lines.append(f"branches_count = {item.get('branches_count', 0)}")
        lines.append(f'rationale = "{item.get("rationale", "")}"')
        lines.append(f'reviewed_by = "{item.get("reviewed_by", "developer")}"')
        lines.append(f'reviewed_at = "{item.get("reviewed_at", now_iso)}"')

        branch_strs = [f'"{b}"' for b in item.get("branches", [])]
        lines.append(f"branches = [{', '.join(branch_strs)}]")
        lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def scan_architectural_splits(db: IndexerDB, project_root: Path) -> list[ArchitecturalSplit]:
    """Scans for functions that branch across multiple modules/packages."""
    with db._get_conn() as conn:
        # Find functions/methods with >= 2 outgoing calls/routes
        query = """
        SELECT
            n.id as node_id,
            n.name as symbol,
            n.qualified_name,
            n.file_path,
            n.start_line,
            n.end_line,
            n.kind,
            COUNT(e.id) as edge_count
        FROM nodes n
        JOIN edges e ON n.id = e.from_node_id
        WHERE n.kind IN ('function', 'method', 'endpoint')
          AND e.relation_type != 'defines'
        GROUP BY n.id
        HAVING COUNT(e.id) >= 2
        ORDER BY edge_count DESC
        """
        rows = conn.execute(query).fetchall()

        splits: list[ArchitecturalSplit] = []

        for r in rows:
            source_file = r["file_path"].replace("\\", "/")
            # Exclude test files from architectural production split audit
            if (
                source_file.startswith("tests/")
                or "/tests/" in source_file
                or source_file.endswith("_test.py")
                or source_file.endswith(".test.ts")
                or source_file.endswith(".test.tsx")
            ):
                continue

            nid = r["node_id"]
            # Fetch target branches
            edge_query = """
            SELECT
                target.name as target_name,
                target.file_path as target_file,
                e.relation_type,
                e.call_line
            FROM edges e
            JOIN nodes target ON e.to_node_id = target.id
            WHERE e.from_node_id = ? AND e.relation_type != 'defines'
            """
            edge_rows = conn.execute(edge_query, (nid,)).fetchall()

            branches = [
                BranchDetail(
                    target_name=er["target_name"],
                    target_file=er["target_file"] or "unknown",
                    relation_type=er["relation_type"],
                    call_line=er["call_line"],
                )
                for er in edge_rows
            ]

            # A bifurcation must have at least 2 branches
            if len(branches) < 2:
                continue

            # Cross-Module filter: Must target at least 2 distinct files
            target_files = {er["target_file"] for er in edge_rows if er["target_file"]}
            if len(target_files) < 2:
                continue

            code_hash = compute_symbol_code_hash(
                r["file_path"],
                r["start_line"],
                r["end_line"],
                project_root,
            )

            splits.append(
                ArchitecturalSplit(
                    node_id=nid,
                    symbol=r["symbol"],
                    qualified_name=r["qualified_name"] or r["symbol"],
                    file_path=r["file_path"],
                    start_line=r["start_line"] or 1,
                    end_line=r["end_line"] or 1,
                    code_hash=code_hash,
                    branches_count=len(branches),
                    branches=branches,
                    status="NEW_UNAPPROVED",
                )
            )

        return splits


def audit_splits(db: IndexerDB, project_root: Path, symbol: str | None = None) -> SplitAuditReport:
    """Audits detected architectural splits against .vibetrace/splits.toml baseline."""
    splits = scan_architectural_splits(db, project_root)
    if symbol:
        splits = [s for s in splits if s.symbol == symbol or s.qualified_name == symbol]

    baseline = load_split_baseline(project_root)

    approved = 0
    modified = 0
    new_unapproved = 0

    for s in splits:
        rec = baseline.get(s.qualified_name) or baseline.get(s.symbol)
        if rec:
            s.baseline_hash = rec.get("code_hash")
            s.rationale = rec.get("rationale")
            s.reviewed_by = rec.get("reviewed_by")
            s.reviewed_at = rec.get("reviewed_at")

            if s.code_hash == s.baseline_hash:
                s.status = rec.get("status", "APPROVED").upper()
                approved += 1
            else:
                s.status = "MODIFIED_CODE"
                modified += 1
        else:
            s.status = "NEW_UNAPPROVED"
            new_unapproved += 1

    return SplitAuditReport(
        total_splits=len(splits),
        approved_count=approved,
        modified_count=modified,
        new_unapproved_count=new_unapproved,
        splits=splits,
    )


def record_split_approval(
    project_root: Path,
    symbol: str,
    qualified_name: str,
    file_path: str,
    code_hash: str,
    branches: list[str],
    status: str = "approved",
    rationale: str = "Architectural review verified",
    reviewed_by: str = "developer",
) -> Path:
    """Records or updates a split approval record in .vibetrace/splits.toml."""
    baseline = load_split_baseline(project_root)
    now_iso = datetime.now(UTC).isoformat()

    new_record = {
        "symbol": symbol,
        "qualified_name": qualified_name,
        "file_path": file_path,
        "status": status,
        "code_hash": code_hash,
        "branches_count": len(branches),
        "branches": branches,
        "rationale": rationale,
        "reviewed_by": reviewed_by,
        "reviewed_at": now_iso,
    }

    baseline[qualified_name] = new_record
    if symbol != qualified_name:
        baseline[symbol] = new_record

    # Unique list of records by qualified_name
    unique_records = {v["qualified_name"]: v for v in baseline.values()}
    return save_split_baseline(project_root, list(unique_records.values()))
