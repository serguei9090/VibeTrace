"""Declarative Semgrep-compatible Quality Rules engine with SQLite TTL caching.

Evaluates clean code, maintainability, correctness, and reliability pattern rules
across Python, TypeScript, JavaScript, Go, Rust, and Dockerfiles.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.taxonomy import CweTaxonomyManager

logger = logging.getLogger(__name__)

DEFAULT_QUALITY_RULES_TTL_DAYS = 7

# Official public Semgrep Community Registry feeds for Maintainability & Correctness
TRUSTED_QUALITY_YAML_FEEDS: dict[str, tuple[str, ...]] = {
    "py": (
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/python/lang/maintainability/return.yaml",
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/python/lang/maintainability/useless-ifelse.yaml",
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/python/lang/correctness/return-in-init.yaml",
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/python/lang/correctness/dict-modify-iterating.yaml",
    ),
    "js": (
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/javascript/lang/best-practice/unhandled-error.yaml",
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/javascript/lang/correctness/useless-assignment.yaml",
    ),
    "ts": (
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/javascript/lang/best-practice/unhandled-error.yaml",
    ),
    "go": (
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/go/lang/maintainability/context.yaml",
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/go/lang/correctness/nilerr.yaml",
    ),
    "docker": (
        "https://raw.githubusercontent.com/semgrep/semgrep-rules/develop/dockerfile/best-practice/missing-user.yaml",
    ),
}

_LANG_ALIAS: dict[str, tuple[str, ...]] = {
    "python": ("py",),
    "py": ("py",),
    "javascript": ("js", "jsx"),
    "js": ("js", "jsx"),
    "typescript": ("ts", "tsx"),
    "ts": ("ts", "tsx"),
    "go": ("go",),
    "golang": ("go",),
    "rust": ("rs",),
    "rs": ("rs",),
    "dockerfile": ("dockerfile", "docker"),
    "docker": ("dockerfile", "docker"),
}


@dataclass(frozen=True)
class DeclarativeQualityRule:
    """Declarative maintainability or correctness rule evaluated against source lines."""

    id: str
    rule_id: str
    rspec_id: str
    cwe_id: str | None
    owasp_category: str | None
    category: QualityCategory
    severity: QualitySeverity
    title: str
    message: str
    pattern: str
    languages: tuple[str, ...]
    compliant_snippet: str = ""
    negative_pattern: str = ""


# Curated offline community rule fallback pack
BUILTIN_DECLARATIVE_QUALITY_RULES: list[DeclarativeQualityRule] = [
    DeclarativeQualityRule(
        id="semgrep.py.return-in-init",
        rule_id="python:S112",
        rspec_id="RSPEC-112",
        cwe_id="CWE-398",
        owasp_category=None,
        category=QualityCategory.RELIABILITY,
        severity=QualitySeverity.HIGH,
        title="Returning a value from `__init__` method causes a runtime TypeError",
        message=(
            "`__init__` should return None. Returning an explicit value "
            "raises TypeError at runtime."
        ),
        pattern=r"def\s+__init__\s*\([^)]*\)\s*:(?:(?!\n\s*def\s|\n[^\s]).)*?\breturn\s+[^\s#\n]+",
        languages=("py",),
        compliant_snippet="def " + "__init__(self):\n    self.val = 42",
    ),
    DeclarativeQualityRule(
        id="semgrep.py.useless-ifelse",
        rule_id="python:S1126",
        rspec_id="RSPEC-1126",
        cwe_id="CWE-1075",
        owasp_category=None,
        category=QualityCategory.MAINTAINABILITY,
        severity=QualitySeverity.LOW,
        title="Boolean literals should not be redundant in if-else statements",
        message=(
            "Return the boolean condition directly instead of "
            "`if cond: return True else: return False`."
        ),
        pattern=r"if\s+.*:\s*\n\s*return\s+(?:True|False)\s*\n\s*else:\s*\n\s*return\s+(?:True|False)",
        languages=("py",),
        compliant_snippet="return bool(condition)",
    ),
    DeclarativeQualityRule(
        id="semgrep.go.nilerr",
        rule_id="go:S391",
        rspec_id="RSPEC-391",
        cwe_id="CWE-391",
        owasp_category=None,
        category=QualityCategory.RELIABILITY,
        severity=QualitySeverity.HIGH,
        title="Returning nil error in an error-handling branch",
        message="Branch checks `err != nil` but returns `nil` instead of `err`.",
        pattern=r"if\s+err\s*!=\s*nil\s*\{\s*return\s+[^,;\n]*,\s*nil\s*\}",
        languages=("go",),
        compliant_snippet="if err != nil {\n    return nil, err\n}",
    ),
    DeclarativeQualityRule(
        id="semgrep.docker.missing-user",
        rule_id="docker:S6437",
        rspec_id="RSPEC-6437",
        cwe_id="CWE-250",
        owasp_category=None,
        category=QualityCategory.RELIABILITY,
        severity=QualitySeverity.MEDIUM,
        title="Containers should not run by default with root user privileges",
        message="Specify a non-root `USER` in the Dockerfile to prevent privilege escalation.",
        pattern=r"^(?:USER\s+root|USER\s+0)\b",
        languages=("dockerfile", "docker"),
        compliant_snippet="USER appuser",
    ),
]


def compile_semgrep_pattern(semgrep_pat: str) -> str:
    """Compiles a Semgrep pattern (`func($X, ..., flag=True)`) into an executable Python regex."""
    s = semgrep_pat.strip()
    parts = re.split(r"(\.\.\.|\$[A-Z_][A-Z0-9_]*)", s)
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if part == "...":
            out.append(r".*?")
        elif part.startswith("$") and len(part) > 1:
            out.append(r"[^,);\n]+")
        else:
            esc = re.escape(part)
            esc = esc.replace(r"\(", r"\s*\(\s*").replace(r"\)", r"\s*\)")
            esc = esc.replace(r"\,", r"\s*,\s*").replace(r"\=", r"\s*=\s*")
            esc = re.sub(r"(\\\s)+", r"\\s+", esc)
            out.append(esc)
    return "".join(out)


def _parse_yaml_list_item(stripped: str, in_list_key: str, current: dict[str, Any]) -> None:
    val = stripped[2:].strip().strip("'\"")
    if val.startswith("pattern:"):
        val = val.split(":", 1)[1].strip().strip("'\"")
    current.setdefault(in_list_key, []).append(val)


def _parse_yaml_kv_line(stripped: str, current: dict[str, Any]) -> str | None:
    k, v = stripped.split(":", 1)
    key = k.lstrip("- ").strip()
    val = v.strip().strip("'\"")
    if not val:
        return key if key in ("languages", "pattern-either", "patterns") else None
    if val.startswith("[") and val.endswith("]"):
        current[key] = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
    else:
        current[key] = val
    return None


def _try_load_yaml(yaml_text: str) -> list[dict[str, Any]] | None:
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(yaml_text)
        if isinstance(loaded, dict) and isinstance(loaded.get("rules"), list):
            return loaded["rules"]
    except Exception:
        pass
    return None


def _start_new_rule(
    stripped: str, current: dict[str, Any] | None, rules: list[dict[str, Any]]
) -> dict[str, Any]:
    if current:
        rules.append(current)
    return {"id": stripped.split(":", 1)[1].strip().strip("'\"")}


def parse_semgrep_yaml(yaml_text: str) -> list[dict[str, Any]]:
    """Parses Semgrep YAML rule definitions safely without external YAML dependencies."""
    if (loaded_rules := _try_load_yaml(yaml_text)) is not None:
        return loaded_rules

    rules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_list_key: str | None = None

    for raw_line in yaml_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "rules:":
            continue
        if stripped.startswith("- id:"):
            current = _start_new_rule(stripped, current, rules)
            in_list_key = None
            continue
        if current is None:
            continue
        if stripped.startswith("- ") and in_list_key:
            _parse_yaml_list_item(stripped, in_list_key, current)
            continue
        if ":" in stripped:
            in_list_key = _parse_yaml_kv_line(stripped, current)

    if current:
        rules.append(current)
    return rules


class DeclarativeQualityRuleManager:
    """Manages downloading, compiling, and evaluating declarative community quality rules."""

    @classmethod
    def _init_cache_table(cls, db: IndexerDB) -> None:
        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_declarative_rules_cache (
                    rule_id TEXT PRIMARY KEY,
                    rule_json TEXT NOT NULL,
                    cached_at INTEGER NOT NULL
                )
                """
            )

    @classmethod
    def get_rules(
        cls, db: IndexerDB | None = None, force_update: bool = False
    ) -> list[DeclarativeQualityRule]:
        """Returns the compiled declarative quality rules from cache or builtins."""
        rules = list(BUILTIN_DECLARATIVE_QUALITY_RULES)
        if db is None:
            return rules

        try:
            cls._init_cache_table(db)
            now = int(time.time())
            ttl_seconds = DEFAULT_QUALITY_RULES_TTL_DAYS * 86400
            with db._get_conn() as conn:
                rows = conn.execute(
                    "SELECT rule_json, cached_at FROM quality_declarative_rules_cache"
                ).fetchall()
                for rule_json, cached_at in rows:
                    if force_update or (now - cached_at) >= ttl_seconds:
                        continue
                    data = json.loads(rule_json)
                    rule = cls._dict_to_rule(data)
                    if rule and not any(r.id == rule.id for r in rules):
                        rules.append(rule)
        except Exception as e:
            logger.debug("Failed reading declarative quality rules from SQLite: %s", e)

        return rules

    @classmethod
    def _dict_to_rule(cls, d: dict[str, Any]) -> DeclarativeQualityRule | None:
        try:
            return DeclarativeQualityRule(
                id=d["id"],
                rule_id=d["rule_id"],
                rspec_id=d.get("rspec_id", d["rule_id"]),
                cwe_id=d.get("cwe_id"),
                owasp_category=d.get("owasp_category"),
                category=QualityCategory(d.get("category", "maintainability")),
                severity=QualitySeverity(d.get("severity", "MEDIUM")),
                title=d["title"],
                message=d["message"],
                pattern=d["pattern"],
                languages=tuple(d.get("languages", ["py"])),
                compliant_snippet=d.get("compliant_snippet", ""),
                negative_pattern=d.get("negative_pattern", ""),
            )
        except Exception:
            return None

    @classmethod
    def _scan_rule_matches(
        cls, rule: DeclarativeQualityRule, content: str, file_path: str
    ) -> list[QualityFinding]:
        is_multiline = "\\n" in rule.pattern or "\n" in rule.pattern or "(?:" in rule.pattern
        flags = re.MULTILINE | (re.DOTALL if is_multiline else 0)
        findings: list[QualityFinding] = []
        try:
            for match in re.finditer(rule.pattern, content, flags=flags):
                snippet = match.group(0).strip()
                if not snippet or snippet.startswith(("#", "//", "/*", "*")):
                    continue
                if rule.negative_pattern and re.search(rule.negative_pattern, snippet):
                    continue
                start_char = match.start()
                end_char = match.end()
                start_line = content.count("\n", 0, start_char) + 1
                end_line = content.count("\n", 0, end_char) + 1
                code_hash = hashlib.sha256(snippet.encode("utf-8")).hexdigest()
                f = QualityFinding(
                    id=f"QUAL-{rule.rspec_id}-{code_hash[:12]}",
                    rule_id=rule.rule_id,
                    rspec_id=rule.rspec_id,
                    cwe_id=rule.cwe_id,
                    cwe_ids=[rule.cwe_id] if rule.cwe_id else [],
                    owasp_category=rule.owasp_category,
                    category=rule.category,
                    severity=rule.severity,
                    title=rule.title,
                    message=rule.message,
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    code_hash=code_hash,
                    non_compliant_snippet=snippet,
                    compliant_snippet=rule.compliant_snippet or None,
                    remediation_guidance=rule.message,
                )
                CweTaxonomyManager.enrich_finding(f)
                findings.append(f)
        except re.error:
            pass
        return findings

    @classmethod
    def scan_file_with_rules(
        cls,
        file_path: str,
        project_root: Path,
        rules: list[DeclarativeQualityRule],
    ) -> list[QualityFinding]:
        """Scans a single file against applicable declarative quality rules."""
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        lower_name = Path(file_path).name.lower()
        if lower_name == "dockerfile" or lower_name.startswith("dockerfile."):
            file_lang = "docker"
        else:
            file_lang = ext

        applicable = [r for r in rules if file_lang in r.languages]
        if not applicable:
            return []

        abs_path = (project_root / file_path).resolve()
        if not abs_path.is_relative_to(project_root.resolve()):
            return []
        try:
            content = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        findings: list[QualityFinding] = []
        for rule in applicable:
            findings.extend(cls._scan_rule_matches(rule, content, file_path))
        return findings
