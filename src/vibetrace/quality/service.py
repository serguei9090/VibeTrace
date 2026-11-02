"""Quality & Maintainability Audit Service coordinating complexity and smells analyzers."""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.quality.adapters.biome import BiomeAdapter
from vibetrace.quality.adapters.golang_lint import GolangLintAdapter
from vibetrace.quality.adapters.php_stan import PhpStanAdapter
from vibetrace.quality.adapters.python_pyright import PyrightAdapter
from vibetrace.quality.adapters.python_ruff import RuffAdapter
from vibetrace.quality.adapters.rubocop import RuboCopAdapter
from vibetrace.quality.adapters.rust_clippy import CargoClippyAdapter
from vibetrace.quality.adapters.shellcheck import ShellCheckAdapter
from vibetrace.quality.adapters.sql_fluff import SqlFluffAdapter
from vibetrace.quality.adapters.typescript_tsc import TypeScriptAdapter
from vibetrace.quality.baseline import QualityBaselineManager
from vibetrace.quality.complexity import (
    FunctionComplexity,
    analyze_python_complexities,
)
from vibetrace.quality.css_analyzer import analyze_css_quality
from vibetrace.quality.declarative_rules import (
    DeclarativeQualityRule,
    DeclarativeQualityRuleManager,
)
from vibetrace.quality.docker_analyzer import analyze_docker_quality
from vibetrace.quality.k8s_analyzer import analyze_k8s_quality
from vibetrace.quality.literal_duplication import analyze_literal_duplications
from vibetrace.quality.models import (
    QualityAuditReport,
    QualityCategory,
    QualityExceptionRecord,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import get_rule_spec, resolve_canonical_rule_id
from vibetrace.quality.smells import analyze_python_smells
from vibetrace.security.regex_analyzer import analyze_regex_quality
from vibetrace.taxonomy import CweTaxonomyManager

_SUPPORTED_EXTS = (
    ".py",
    ".css",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".mts",
    ".cts",
    ".json",
    ".jsonc",
    ".go",
    ".rs",
    ".php",
    ".phtml",
    ".rb",
    ".sh",
    ".bash",
    ".yaml",
    ".yml",
    ".sql",
)
_IGNORE_PATH_PARTS = (
    "/.venv/",
    "/venv/",
    "/__pycache__/",
    "/.git/",
    "/scratch/",
    "/node_modules/",
    "/dist/",
    "/build/",
    "/out/",
    "/.next/",
    "/target/",
)
_INLINE_IGNORE_RE = re.compile(
    r"(?:#|//)\s*(?:nosonar|noqa(?::\s*([a-z0-9_-]+))?|vibetrace:ignore\b(?:\s*\[([a-z0-9_:-]+)\])?(?:\s*:\s*(.+))?)",
    re.IGNORECASE,
)


def _is_suppressed_by_line(finding: QualityFinding, line: str, rule_key: str) -> bool:
    """Evaluates whether a single line matches an inline suppression for this rule."""
    m = _INLINE_IGNORE_RE.search(line)
    if not m:
        return False
    matched_rule = m.group(1) or m.group(2)
    if matched_rule and resolve_canonical_rule_id(matched_rule) != rule_key:
        return False
    finding.status = "APPROVED"
    finding.tag = "inline-comment"
    finding.rationale = (m.group(3) or "Suppressed via inline comment").strip()
    finding.reviewed_by = "inline-comment"
    return True


def _check_inline_suppression(finding: QualityFinding, source_lines: list[str]) -> bool:
    """Checks if finding has an inline suppression comment immediately above or on the line."""
    target_idx = finding.start_line - 1
    rule_key = resolve_canonical_rule_id(finding.rule_id)
    for idx in (target_idx - 2, target_idx - 1, target_idx):
        if 0 <= idx < len(source_lines) and _is_suppressed_by_line(
            finding, source_lines[idx].strip(), rule_key
        ):
            return True
    return False


def _resolve_candidate_paths(
    project_root: Path,
    file_filter: str | None = None,
    file_list: list[str] | None = None,
) -> list[Path]:
    """Resolves candidate source files (.py, .css, .ts, .tsx, Dockerfile)."""
    if file_list:
        return [
            p
            for p_str in file_list
            if (p := (project_root / p_str).resolve()).is_file()
            and (
                p_str.endswith(_SUPPORTED_EXTS)
                or p.name.lower() == "dockerfile"
                or p.name.lower().startswith("dockerfile.")
            )
        ]
    if file_filter:
        target = (project_root / file_filter).resolve()
        if target.is_file():
            return [target]
        if target.is_dir():
            found: list[Path] = []
            for ext in _SUPPORTED_EXTS:
                found.extend(target.rglob(f"*{ext}"))
            found.extend(target.rglob("Dockerfile*"))
            return found
        return []

    src_dir = project_root / "src"
    search_root = src_dir if src_dir.is_dir() else project_root
    found_all: list[Path] = []
    for ext in _SUPPORTED_EXTS:
        found_all.extend(search_root.rglob(f"*{ext}"))
    found_all.extend(search_root.rglob("Dockerfile*"))
    return found_all


def _is_path_allowed(path: Path, include_tests: bool) -> bool:
    """Checks whether a resolved path is allowed for quality auditing."""
    posix_path = path.as_posix()
    if any(seg in posix_path for seg in _IGNORE_PATH_PARTS):
        return False
    if not include_tests:
        parts = posix_path.split("/")
        if any(p in ("tests", "test", "e2e", "__tests__") for p in parts[:-1]):
            return False
        fname = path.name.lower()
        if fname.startswith("test_") or fname.endswith(
            ("_test.py", "_test.go", ".spec.ts", ".test.ts", ".spec.tsx", ".test.tsx")
        ):
            return False
    return True


def _collect_source_files(
    project_root: Path,
    file_filter: str | None = None,
    file_list: list[str] | None = None,
    include_tests: bool = False,
) -> list[Path]:
    """Resolves and filters all target files for quality auditing."""
    candidates = _resolve_candidate_paths(project_root, file_filter, file_list)
    return [p for p in candidates if _is_path_allowed(p, include_tests)]


def _audit_single_file(
    file_path: Path,
    project_root: Path,
    complexity_threshold: int,
    decl_rules: list[DeclarativeQualityRule] | None = None,
) -> tuple[list[FunctionComplexity], list[QualityFinding]]:
    """Analyzes a single source file (.py, .css, .ts, .tsx, Dockerfile) for quality smells."""
    try:
        rel_path = file_path.relative_to(project_root).as_posix()
    except ValueError:
        rel_path = file_path.as_posix()

    try:
        source_code = file_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return [], []

    source_lines = source_code.splitlines()
    lower_name = file_path.name.lower()
    lower_suffix = file_path.suffix.lower()

    comps: list[FunctionComplexity] = []
    findings: list[QualityFinding] = []

    if lower_suffix == ".css":
        findings = analyze_css_quality(file_path=rel_path, source_code=source_code)
    elif lower_suffix in (
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".json",
        ".jsonc",
        ".go",
        ".rs",
        ".sh",
        ".bash",
        ".sql",
    ):
        # Delegated to specialized sub-engine adapters
        findings = []
    elif lower_suffix in (".yaml", ".yml"):
        findings = analyze_k8s_quality(file_path=rel_path, source_code=source_code)
    elif lower_name == "dockerfile" or lower_name.startswith("dockerfile."):
        findings = analyze_docker_quality(file_path=rel_path, source_code=source_code)
    elif lower_suffix in (".php", ".phtml", ".rb"):
        from vibetrace.indexer.parsers import ParserFactory
        from vibetrace.quality.treesitter_complexity import analyze_treesitter_quality

        lang_prefix = "ruby" if lower_suffix == ".rb" else "php"
        parser = ParserFactory.get_parser(file_path.as_posix())
        if parser:
            try:
                tree = parser.parse(source_code.encode("utf-8", errors="replace"))
                comps, findings = analyze_treesitter_quality(
                    tree.root_node,
                    file_path=rel_path,
                    source_lines=source_lines,
                    complexity_threshold=complexity_threshold,
                    language_prefix=lang_prefix,
                )
            except Exception:
                comps, findings = [], []
    else:
        file_comps, comp_findings = analyze_python_complexities(
            file_path=rel_path,
            source_code=source_code,
            threshold=complexity_threshold,
        )
        comps = file_comps
        findings.extend(comp_findings)

        # Ingest duplicated string literals (RSPEC-1192)
        literal_findings = analyze_literal_duplications(
            file_path=rel_path,
            source_code=source_code,
        )
        findings.extend(literal_findings)

        # Ingest regex maintainability smells (RSPEC-6353, RSPEC-5843)
        regex_findings = analyze_regex_quality(
            file_path=rel_path,
            source_code=source_code,
        )
        for rf in regex_findings:
            if rf.rule_id in ("python:S6353", "python:S5843"):
                findings.append(
                    QualityFinding(
                        id=f"QUAL-{rf.rule_id.split(':')[-1]}-{rf.code_hash[:12]}",
                        rule_id=rf.rule_id,
                        rspec_id=f"RSPEC-{rf.rule_id.split(':')[-1][1:]}",
                        category=QualityCategory.CODE_SMELL,
                        severity=(
                            QualitySeverity.LOW
                            if rf.rule_id == "python:S6353"
                            else QualitySeverity.MEDIUM
                        ),
                        title=rf.title,
                        message=rf.message,
                        file_path=rf.file_path,
                        start_line=rf.start_line,
                        end_line=rf.end_line,
                        symbol_name=rf.symbol_name,
                        code_hash=rf.code_hash,
                        non_compliant_snippet=rf.non_compliant_snippet,
                        compliant_snippet=rf.compliant_snippet,
                        remediation_guidance=rf.remediation_guidance,
                    )
                )

        ruff_engine = RuffAdapter()
        if not ruff_engine.is_available(project_root):
            smell_findings = analyze_python_smells(
                file_path=rel_path,
                source_code=source_code,
            )
            findings.extend(smell_findings)

    if decl_rules:
        decl_findings = DeclarativeQualityRuleManager.scan_file_with_rules(
            rel_path, project_root, decl_rules
        )
        findings.extend(decl_findings)

    for f in findings:
        _check_inline_suppression(f, source_lines)

    return comps, findings


class QualityService:
    """High-level service coordinating code quality and cognitive complexity audits."""

    @classmethod
    def audit_project(
        cls,
        db: IndexerDB | None,
        project_root: Path,
        file_filter: str | None = None,
        file_list: list[str] | None = None,
        complexity_threshold: int = 15,
        include_tests: bool = False,
        typecheck: bool = False,
    ) -> QualityAuditReport:
        """Audits project source files for cognitive complexity and clean code smells."""
        raw_findings: list[QualityFinding] = []
        all_complexities: list[FunctionComplexity] = []

        target_files = _collect_source_files(
            project_root=project_root,
            file_filter=file_filter,
            file_list=file_list,
            include_tests=include_tests,
        )

        decl_rules = DeclarativeQualityRuleManager.get_rules(db=db)

        for file_path in target_files:
            file_comps, file_findings = _audit_single_file(
                file_path=file_path,
                project_root=project_root,
                complexity_threshold=complexity_threshold,
                decl_rules=decl_rules,
            )
            all_complexities.extend(file_comps)
            raw_findings.extend(file_findings)

        # Batch execute native linter sub-engines (Ruff, Pyright) if available
        py_files = [p for p in target_files if p.suffix.lower() == ".py"]
        if py_files:
            ruff_engine = RuffAdapter()
            if ruff_engine.is_available(project_root):
                raw_findings.extend(ruff_engine.audit_project(project_root, py_files))

            pyright_engine = PyrightAdapter()
            if pyright_engine.is_available(project_root):
                raw_findings.extend(pyright_engine.audit_project(project_root, py_files))

        # Batch execute native Biome sub-engine for JS/TS/JSON/CSS if available
        biome_files = [
            p for p in target_files if p.suffix.lower() in BiomeAdapter.SUPPORTED_EXTENSIONS
        ]
        if biome_files:
            biome_engine = BiomeAdapter()
            if biome_engine.is_available(project_root):
                raw_findings.extend(biome_engine.audit_project(project_root, biome_files))

        # Batch execute TypeScript incremental typechecker if requested or configured
        if typecheck:
            supported = TypeScriptAdapter.SUPPORTED_EXTENSIONS
            ts_files = [p for p in target_files if p.suffix.lower() in supported]
            if ts_files:
                tsc_engine = TypeScriptAdapter()
                if tsc_engine.is_available(project_root):
                    raw_findings.extend(tsc_engine.audit_project(project_root, ts_files))

        # Batch execute Go linter sub-engine if available
        go_files = [
            p for p in target_files if p.suffix.lower() in GolangLintAdapter.SUPPORTED_EXTENSIONS
        ]
        if go_files:
            go_engine = GolangLintAdapter()
            if go_engine.is_available(project_root):
                raw_findings.extend(go_engine.audit_project(project_root, go_files))

        # Batch execute Rust Cargo Clippy sub-engine if available
        rust_files = [
            p for p in target_files if p.suffix.lower() in CargoClippyAdapter.SUPPORTED_EXTENSIONS
        ]
        if rust_files:
            rust_engine = CargoClippyAdapter()
            if rust_engine.is_available(project_root):
                raw_findings.extend(rust_engine.audit_project(project_root, rust_files))

        # Batch execute PHPStan sub-engine if available
        php_files = [
            p for p in target_files if p.suffix.lower() in PhpStanAdapter.SUPPORTED_EXTENSIONS
        ]
        if php_files:
            php_engine = PhpStanAdapter()
            if php_engine.is_available(project_root):
                raw_findings.extend(php_engine.audit_project(project_root, php_files))

        # Batch execute RuboCop sub-engine if available
        ruby_files = [
            p for p in target_files if p.suffix.lower() in RuboCopAdapter.SUPPORTED_EXTENSIONS
        ]
        if ruby_files:
            rubo_engine = RuboCopAdapter()
            if rubo_engine.is_available(project_root):
                raw_findings.extend(rubo_engine.audit_project(project_root, ruby_files))

        # Batch execute ShellCheck sub-engine if available
        shell_files = [
            p for p in target_files if p.suffix.lower() in ShellCheckAdapter.SUPPORTED_EXTENSIONS
        ]
        if shell_files:
            shell_engine = ShellCheckAdapter()
            if shell_engine.is_available(project_root):
                raw_findings.extend(shell_engine.audit_project(project_root, shell_files))

        # Batch execute SQLFluff sub-engine if available
        sql_files = [
            p for p in target_files if p.suffix.lower() in SqlFluffAdapter.SUPPORTED_EXTENSIONS
        ]
        if sql_files:
            sql_engine = SqlFluffAdapter()
            if sql_engine.is_available(project_root):
                raw_findings.extend(sql_engine.audit_project(project_root, sql_files))

        # Collect dead / uncalled private functions from Call Graph (RSPEC-1144)
        if db is not None:
            dead_nodes = db.get_uncalled_private_functions(include_tests=include_tests)
            target_rel_paths = {p.relative_to(project_root).as_posix() for p in target_files}
            for dn in dead_nodes:
                dn_file = dn["file_path"].replace("\\", "/")
                while dn_file.startswith("./"):
                    dn_file = dn_file[2:]
                if target_rel_paths and dn_file not in target_rel_paths:
                    continue
                hash_id = hashlib.sha256(f"{dn_file}:{dn['name']}".encode()).hexdigest()[:12]
                ext = Path(dn_file).suffix.lower()
                prefix = (
                    "php" if ext in (".php", ".phtml") else "ruby" if ext == ".rb" else "python"
                )
                raw_findings.append(
                    QualityFinding(
                        id=f"QUAL-S1144-{hash_id}",
                        rule_id=f"{prefix}:S1144",
                        rspec_id="RSPEC-1144",
                        category=QualityCategory.MAINTAINABILITY,
                        severity=QualitySeverity.MEDIUM,
                        title=f"Unused private {dn['kind']} '{dn['name']}' should be removed.",
                        message=(
                            f"Private {dn['kind']} '{dn['qualified_name']}' is never called "
                            f"or referenced in the call graph."
                        ),
                        file_path=dn_file,
                        start_line=dn["start_line"] or 1,
                        end_line=dn["end_line"] or dn["start_line"] or 1,
                        symbol_name=dn["qualified_name"],
                        code_hash=hashlib.sha256(dn["name"].encode()).hexdigest(),
                        non_compliant_snippet=dn["signature"] or f"def {dn['name']}(...):",
                        compliant_snippet=None,
                        remediation_guidance=(
                            f"Remove the unused private {dn['kind']} '{dn['name']}' or wire it "
                            f"into caller execution paths."
                        ),
                    )
                )

        # Deduplicate findings by (file_path, start_line, rule_id)
        seen_keys: set[tuple[str, int, str]] = set()
        deduped_findings: list[QualityFinding] = []
        for f in raw_findings:
            key = (f.file_path, f.start_line, f.rule_id)
            if key not in seen_keys:
                seen_keys.add(key)
                deduped_findings.append(f)
        raw_findings = deduped_findings

        # Correlate symbol and call-graph context with IndexerDB if available
        if db:
            for f in raw_findings:
                if not f.symbol_name:
                    nodes_in_file = db.get_nodes_in_file(f.file_path)
                    for n in nodes_in_file:
                        s_l = int(n.get("start_line") or 0)
                        e_l = int(n.get("end_line") or 0)
                        if s_l <= f.start_line <= e_l:
                            f.symbol_name = n.get("name")
                            break

        # Check inline comment suppressions on all collected findings
        for f in raw_findings:
            if f.status != "APPROVED":
                file_p = project_root / f.file_path
                if file_p.is_file():
                    try:
                        s_lines = file_p.read_text(encoding="utf-8", errors="replace").splitlines()
                        _check_inline_suppression(f, s_lines)
                    except OSError:
                        pass

        for f in raw_findings:
            spec = get_rule_spec(f.rule_id)
            if spec:
                if spec.cwe_ids and not f.cwe_ids:
                    f.cwe_ids = list(spec.cwe_ids)
                    f.cwe_id = spec.cwe_ids[0]
                if spec.owasp and not f.owasp_category:
                    f.owasp_category = spec.owasp
            CweTaxonomyManager.enrich_finding(f)

        report = QualityBaselineManager.apply_baseline_to_findings(
            findings=raw_findings,
            project_root=project_root,
        )

        if all_complexities:
            report.average_complexity = sum(c.cognitive_complexity for c in all_complexities) / len(
                all_complexities
            )
            max_comp = max(all_complexities, key=lambda c: c.cognitive_complexity)
            report.max_complexity = max_comp.cognitive_complexity
            report.max_complexity_symbol = max_comp.name

        return report

    @classmethod
    def fix_project(
        cls,
        project_root: Path,
        file_filter: str | None = None,
        file_list: list[str] | None = None,
        include_tests: bool = False,
    ) -> dict[str, Any]:
        """Applies automated safe fixes via Ruff and Biome sub-engines."""
        target_files = _collect_source_files(
            project_root=project_root,
            file_filter=file_filter,
            file_list=file_list,
            include_tests=include_tests,
        )
        results: dict[str, Any] = {"engines": {}, "total_files": len(target_files)}
        py_files = [p for p in target_files if p.suffix.lower() == ".py"]
        if py_files:
            ruff_engine = RuffAdapter()
            if ruff_engine.is_available(project_root):
                results["engines"]["ruff"] = ruff_engine.fix_files(project_root, py_files)

        biome_files = [
            p for p in target_files if p.suffix.lower() in BiomeAdapter.SUPPORTED_EXTENSIONS
        ]
        if biome_files:
            biome_engine = BiomeAdapter()
            if biome_engine.is_available(project_root):
                results["engines"]["biome"] = biome_engine.fix_files(project_root, biome_files)

        go_files = [
            p for p in target_files if p.suffix.lower() in GolangLintAdapter.SUPPORTED_EXTENSIONS
        ]
        if go_files:
            go_engine = GolangLintAdapter()
            if go_engine.is_available(project_root):
                results["engines"]["golang"] = go_engine.fix_files(project_root, go_files)

        rust_files = [
            p for p in target_files if p.suffix.lower() in CargoClippyAdapter.SUPPORTED_EXTENSIONS
        ]
        if rust_files:
            rust_engine = CargoClippyAdapter()
            if rust_engine.is_available(project_root):
                results["engines"]["rust"] = rust_engine.fix_files(project_root, rust_files)

        php_files = [
            p for p in target_files if p.suffix.lower() in PhpStanAdapter.SUPPORTED_EXTENSIONS
        ]
        if php_files:
            php_engine = PhpStanAdapter()
            if php_engine.is_available(project_root):
                results["engines"]["php"] = php_engine.fix_files(project_root, php_files)

        ruby_files = [
            p for p in target_files if p.suffix.lower() in RuboCopAdapter.SUPPORTED_EXTENSIONS
        ]
        if ruby_files:
            rubo_engine = RuboCopAdapter()
            if rubo_engine.is_available(project_root):
                results["engines"]["ruby"] = rubo_engine.fix_files(project_root, ruby_files)

        sql_files = [
            p for p in target_files if p.suffix.lower() in SqlFluffAdapter.SUPPORTED_EXTENSIONS
        ]
        if sql_files:
            sql_engine = SqlFluffAdapter()
            if sql_engine.is_available(project_root):
                results["engines"]["sql"] = sql_engine.fix_files(project_root, sql_files)

        return results

    @classmethod
    def apply_auto_comments(
        cls,
        project_root: Path,
        findings: list[QualityFinding],
        rationale: str = "Vibecoding approved",
    ) -> int:
        """Injects inline suppression comments above flagged AST nodes."""
        by_file: dict[str, list[QualityFinding]] = defaultdict(list)
        for f in findings:
            if f.status in ("UNAPPROVED", "MODIFIED_CODE"):
                by_file[f.file_path].append(f)

        commented_count = 0
        for rel_path, file_findings in by_file.items():
            abs_path = (project_root / rel_path).resolve()
            if not abs_path.is_file():
                continue
            lines = abs_path.read_text(encoding="utf-8").splitlines()
            sorted_f = sorted(file_findings, key=lambda x: x.start_line, reverse=True)
            for f in sorted_f:
                idx = max(0, f.start_line - 1)
                indent = ""
                if idx < len(lines):
                    line_text = lines[idx]
                    indent = line_text[: len(line_text) - len(line_text.lstrip())]
                rule_short = f.rule_id.split(":")[-1]
                comment_line = f"{indent}# vibetrace:ignore[{rule_short}]: {rationale}  # NOSONAR"
                lines.insert(idx, comment_line)
                commented_count += 1
            abs_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        return commented_count

    @classmethod
    def record_exception(
        cls,
        project_root: Path,
        record: QualityExceptionRecord | None = None,
        **kwargs: Any,
    ) -> None:
        """Records an approved quality exception in .vibetrace/quality.toml."""
        QualityBaselineManager.record_exception(
            project_root=project_root,
            record=record,
            **kwargs,
        )
