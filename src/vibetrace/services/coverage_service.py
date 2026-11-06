"""Hybrid Test Coverage & SonarQube-style Quality Gate Engine for VibeTrace."""

from __future__ import annotations

import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

from pydantic import BaseModel, Field

from vibetrace.indexer.db import IndexerDB


class UntestedSymbol(BaseModel):
    """Represents a modified or newly added function/method without test coverage."""

    symbol: str
    file_path: str
    start_line: int


class CoverageGateReport(BaseModel):
    """Combined AST symbol test reachability and optional Cobertura/LCOV line coverage report."""

    threshold_pct: float = 80.0
    enforced: bool = False
    passed: bool = True
    status: str = "N/A"
    total_target_symbols: int = 0
    tested_symbols_count: int = 0
    ast_symbol_coverage_pct: float | None = None
    line_coverage_pct: float | None = None
    effective_coverage_pct: float | None = None
    coverage_source: str = "ast_call_graph"
    untested_symbols: list[UntestedSymbol] = Field(default_factory=list)


def load_quality_gate_config(project_root: Path) -> tuple[float, bool]:
    """Reads optional [quality_gate] min_coverage and enforce_coverage from config.toml."""
    cfg_path = project_root / ".vibetrace" / "config.toml"
    if not cfg_path.is_file():
        return 80.0, False
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        qg = data.get("quality_gate", {})
        min_cov = float(qg.get("min_coverage", 80.0))
        enforce = bool(qg.get("enforce_coverage", False))
        return min_cov, enforce
    except Exception:
        return 80.0, False


def _is_test_symbol_or_file(name: str, file_path: str) -> bool:
    norm = (file_path or "").replace("\\", "/").lower()
    parts = norm.split("/")
    fname = parts[-1] if parts else ""
    if (
        any(
            p in ("tests", "test", "e2e", "__tests__", "codeingesttest", "fixtures")
            for p in parts[:-1]
        )
        or fname.startswith("test_")
        or fname.endswith(("_test.py", "_test.go", ".spec.ts", ".test.ts", ".spec.tsx"))
    ):
        return True
    low_name = (name or "").lower()
    return low_name.startswith(("test_", "test"))


def _parse_external_line_coverage(
    project_root: Path,
    changed_files: set[str],
    file_ranges: dict[str, list[tuple[int, int]]],
) -> tuple[float | None, str | None]:
    """Parses Cobertura coverage.xml or lcov.info if present on disk."""
    cov_xml = project_root / "coverage.xml"
    if cov_xml.is_file():
        try:
            tree = ET.parse(cov_xml)
            root = tree.getroot()
            hits_total = 0
            lines_total = 0
            for cls_el in root.iter("class"):
                fname = (cls_el.attrib.get("filename") or "").replace("\\", "/")
                if changed_files and fname not in changed_files:
                    continue
                ranges = file_ranges.get(fname)
                for line_el in cls_el.iter("line"):
                    line_no = int(line_el.attrib.get("number", "0"))
                    hits = int(line_el.attrib.get("hits", "0"))
                    if ranges and not any(r_s <= line_no <= r_e for r_s, r_e in ranges):
                        continue
                    lines_total += 1
                    if hits > 0:
                        hits_total += 1
            if lines_total > 0:
                return round((hits_total / lines_total) * 100.0, 1), "coverage.xml"
        except Exception:
            pass

    for lcov_rel in ("lcov.info", "coverage/lcov.info"):
        lcov_path = project_root / lcov_rel
        if lcov_path.is_file():
            try:
                cur_file = ""
                hits_total = 0
                lines_total = 0
                for raw in lcov_path.read_text(encoding="utf-8", errors="replace").splitlines():
                    if raw.startswith("SF:"):
                        sf = raw[3:].strip().replace("\\", "/")
                        try:
                            cur_file = str(
                                Path(sf).resolve().relative_to(project_root.resolve())
                            ).replace("\\", "/")
                        except Exception:
                            cur_file = sf
                    elif raw.startswith("DA:") and cur_file:
                        if changed_files and cur_file not in changed_files:
                            continue
                        parts = raw[3:].split(",")
                        if len(parts) >= 2:
                            line_no = int(parts[0])
                            hits = int(parts[1])
                            ranges = file_ranges.get(cur_file)
                            if ranges and not any(r_s <= line_no <= r_e for r_s, r_e in ranges):
                                continue
                            lines_total += 1
                            if hits > 0:
                                hits_total += 1
                if lines_total > 0:
                    return round((hits_total / lines_total) * 100.0, 1), lcov_rel
            except Exception:
                pass

    return None, None


class CoverageService:
    """Computes instant AST test-caller reachability + optional Cobertura/LCOV line coverage."""

    @classmethod
    def evaluate_coverage_gate(
        cls,
        db: IndexerDB,
        project_root: Path,
        scope: str,
        changed_files: set[str],
        file_ranges: dict[str, list[tuple[int, int]]],
        min_coverage: float | None = None,
        enforce: bool = False,
    ) -> CoverageGateReport:
        cfg_min, cfg_enforce = load_quality_gate_config(project_root)
        threshold = min_coverage if min_coverage is not None else cfg_min
        enforced = enforce or cfg_enforce

        with db._get_conn() as conn:
            node_rows = conn.execute(
                "SELECT id, name, kind, file_path, start_line, end_line FROM nodes "
                "WHERE kind IN ('function', 'method')"
            ).fetchall()
            edge_rows = conn.execute(
                "SELECT from_node_id, to_node_id FROM edges WHERE relation_type = 'calls'"
            ).fetchall()

        node_by_id: dict[str, dict] = {}
        test_node_ids: set[str] = set()
        for r in node_rows:
            nid, name, kind, fp, s_ln, e_ln = (
                r[0],
                r[1],
                r[2],
                (r[3] or "").replace("\\", "/"),
                r[4] or 1,
                r[5] or 1,
            )
            info = {
                "id": nid,
                "name": name or "",
                "kind": kind,
                "file_path": fp,
                "start_line": s_ln,
                "end_line": e_ln,
            }
            node_by_id[nid] = info
            if _is_test_symbol_or_file(info["name"], fp):
                test_node_ids.add(nid)

        # Forward call graph from all test functions (up to 3 hops)
        callees_by_src: dict[str, set[str]] = {}
        for src_id, tgt_id in edge_rows:
            callees_by_src.setdefault(src_id, set()).add(tgt_id)

        covered_ids: set[str] = set(test_node_ids)
        frontier: set[str] = set(test_node_ids)
        for _ in range(3):
            next_frontier: set[str] = set()
            for u in frontier:
                for v in callees_by_src.get(u, ()):
                    if v not in covered_ids:
                        covered_ids.add(v)
                        next_frontier.append(v) if isinstance(
                            next_frontier, list
                        ) else next_frontier.add(v)
            if not next_frontier:
                break
            frontier = next_frontier

        # Also check if any test file references the function name directly
        covered_names: set[str] = {
            node_by_id[nid]["name"] for nid in covered_ids if nid in node_by_id
        }

        target_nodes: list[dict] = []
        for info in node_by_id.values():
            fp = info["file_path"]
            name = info["name"]
            if not fp or _is_test_symbol_or_file(name, fp) or name.startswith("_"):
                continue
            if scope != "full":
                if not changed_files or fp not in changed_files:
                    continue
                ranges = file_ranges.get(fp)
                if ranges and not any(
                    info["start_line"] <= r_e and info["end_line"] >= r_s for r_s, r_e in ranges
                ):
                    continue
            target_nodes.append(info)

        tested_count = 0
        untested: list[UntestedSymbol] = []
        for info in target_nodes:
            if info["id"] in covered_ids or info["name"] in covered_names:
                tested_count += 1
            else:
                untested.append(
                    UntestedSymbol(
                        symbol=info["name"],
                        file_path=info["file_path"],
                        start_line=info["start_line"],
                    )
                )

        total_targets = len(target_nodes)
        ast_pct: float | None = (
            round((tested_count / total_targets) * 100.0, 1) if total_targets > 0 else None
        )

        line_pct, ext_src = _parse_external_line_coverage(project_root, changed_files, file_ranges)
        if line_pct is not None and ext_src:
            effective_pct: float | None = line_pct
            cov_source = f"hybrid (ast + {ext_src})"
        else:
            effective_pct = ast_pct
            cov_source = "ast_call_graph"

        gate_passed = True
        if effective_pct is None:
            gate_status = "N/A"
        elif effective_pct < threshold:
            gate_status = "FAILED" if enforced else "ADVISORY"
            if enforced and total_targets > 0:
                gate_passed = False
        else:
            gate_status = "PASSED"

        return CoverageGateReport(
            threshold_pct=threshold,
            enforced=enforced,
            passed=gate_passed,
            status=gate_status,
            total_target_symbols=total_targets,
            tested_symbols_count=tested_count,
            ast_symbol_coverage_pct=ast_pct,
            line_coverage_pct=line_pct,
            effective_coverage_pct=effective_pct,
            coverage_source=cov_source,
            untested_symbols=untested,
        )
