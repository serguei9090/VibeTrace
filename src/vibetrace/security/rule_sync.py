"""Declarative security rule catalog, SQLite TTL cache, and auto-updating feed sync."""

from __future__ import annotations

import hashlib
import json
import re
import time
import tomllib
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.rules import RULES_CATALOG

DEFAULT_RULES_TTL_DAYS = 7
DEFAULT_RULES_FEED_URL = (
    "https://raw.githubusercontent.com/vibetrace/security-rules/main/catalog.json"
)
_NOSEC_RE = re.compile(
    r"(?:#|//)\s*(?:nosec|vibetrace:ignore)\b(?:\s*\[([a-z0-9_-]+)\])?(?:\s*:\s*(.+))?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeclarativeRule:
    id: str
    cwe_id: str
    severity: Severity
    title: str
    message: str
    pattern: str
    languages: tuple[str, ...]
    compliant_snippet: str


BUILTIN_DECLARATIVE_RULES: list[DeclarativeRule] = [
    DeclarativeRule(
        id="web:CWE-79-xss",
        cwe_id="CWE-79",
        severity=Severity.HIGH,
        title="Improper Neutralization of Input During Web Page Generation ('XSS')",
        message=(
            "Unescaped HTML assignment ('dangerouslySetInnerHTML' / '.innerHTML' / 'mark_safe')."
        ),
        pattern=r"(?:dangerouslySetInnerHTML|\.innerHTML\s*=|\bmark_safe\s*\()",
        languages=("py", "js", "ts", "tsx", "jsx"),
        compliant_snippet="el.textContent = userText; // or sanitize with DOMPurify",
    ),
    DeclarativeRule(
        id="net:CWE-918-ssrf",
        cwe_id="CWE-918",
        severity=Severity.HIGH,
        title="Server-Side Request Forgery (SSRF)",
        message="HTTP client called with dynamically interpolated URL string.",
        pattern=(
            r"\b(?:requests|httpx|axios)\.(?:get|post|put|delete|request)\s*\(\s*"
            r"(?:f['\"]|`https?://\$\{)"
        ),
        languages=("py", "js", "ts", "tsx"),
        compliant_snippet="Validate hostname against an explicit allowlist before requesting.",
    ),
    DeclarativeRule(
        id="tls:CWE-295-cert",
        cwe_id="CWE-295",
        severity=Severity.MEDIUM,
        title="Improper Certificate Validation",
        message=(
            "TLS certificate verification disabled ('verify=False' / 'rejectUnauthorized: false')."
        ),
        pattern=(
            r"(?:\bverify\s*=\s*False\b|rejectUnauthorized\s*:\s*false\b"
            r"|_create_unverified_context\s*\()"
        ),
        languages=("py", "js", "ts", "tsx"),
        compliant_snippet="Keep TLS certificate verification enabled (verify=True).",
    ),
    DeclarativeRule(
        id="jwt:CWE-347-unverified",
        cwe_id="CWE-347",
        severity=Severity.HIGH,
        title="Improper Verification of Cryptographic Signature ('Unverified JWT')",
        message="JWT decoded with signature verification disabled.",
        pattern=r"jwt\.decode\s*\([^)]*(?:['\"]verify_signature['\"]\s*:\s*False|verify\s*=\s*False)",
        languages=("py", "js", "ts"),
        compliant_snippet="jwt.decode(token, key=PUBLIC_KEY, algorithms=['RS256'])",
    ),
    DeclarativeRule(
        id="xml:CWE-611-xxe",
        cwe_id="CWE-611",
        severity=Severity.HIGH,
        title="Improper Restriction of XML External Entity Reference ('XXE')",
        message="XML parser configured with 'resolve_entities=True'.",
        pattern=r"XMLParser\s*\([^)]*resolve_entities\s*=\s*True",
        languages=("py",),
        compliant_snippet="lxml.etree.XMLParser(resolve_entities=False, no_network=True)",
    ),
]


class RuleCatalogManager:
    """Manages built-in, local (.vibetrace/rules/), and auto-synced SQLite rule catalogs."""

    @classmethod
    def _ensure_table(cls, db: IndexerDB) -> None:
        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS security_rules_cache (
                    rule_id TEXT PRIMARY KEY,
                    rule_json TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    @classmethod
    def get_sync_config(cls, project_root: Path) -> tuple[int, str]:
        """Reads `[sync] rules_ttl_days` and `rules_feed_url` from .vibetrace/security.toml."""
        toml_path = project_root / ".vibetrace" / "security.toml"
        if not toml_path.is_file():
            return DEFAULT_RULES_TTL_DAYS, DEFAULT_RULES_FEED_URL
        try:
            raw = tomllib.loads(toml_path.read_text(encoding="utf-8"))
            sync_cfg = raw.get("sync", {})
            ttl_days = int(sync_cfg.get("rules_ttl_days", DEFAULT_RULES_TTL_DAYS))
            feed_url = str(sync_cfg.get("rules_feed_url", DEFAULT_RULES_FEED_URL))
            return max(1, ttl_days), feed_url
        except Exception:
            return DEFAULT_RULES_TTL_DAYS, DEFAULT_RULES_FEED_URL

    @classmethod
    def sync_and_load_rules(
        cls,
        db: IndexerDB,
        project_root: Path,
        force_update: bool = False,
        fetcher: Any = None,
    ) -> list[DeclarativeRule]:
        """Loads built-in rules, auto-syncs remote catalog on TTL expiry, and merges local rules."""
        cls._ensure_table(db)
        ttl_days, feed_url = cls.get_sync_config(project_root)
        ttl_secs = ttl_days * 86400.0
        now = time.time()

        with db._get_conn() as conn:
            row = conn.execute("SELECT MIN(updated_at) FROM security_rules_cache").fetchone()
            oldest_ts = float(row[0]) if row and row[0] is not None else 0.0

        needs_sync = (
            force_update
            or (oldest_ts == 0.0 and fetcher is not None)
            or (oldest_ts > 0.0 and (now - oldest_ts) > ttl_secs)
        )
        if needs_sync and feed_url:
            cls._refresh_from_feed(db, feed_url, now, fetcher=fetcher)

        rules_by_id: dict[str, DeclarativeRule] = {r.id: r for r in BUILTIN_DECLARATIVE_RULES}

        # Merge cached feed rules from SQLite
        with db._get_conn() as conn:
            for (rule_json,) in conn.execute(
                "SELECT rule_json FROM security_rules_cache"
            ).fetchall():
                try:
                    parsed = cls._rule_from_dict(json.loads(rule_json))
                    if parsed:
                        rules_by_id[parsed.id] = parsed
                except Exception:
                    continue

        # Merge local custom project rules (.vibetrace/rules/*.json)
        rules_dir = project_root / ".vibetrace" / "rules"
        if rules_dir.is_dir():
            for jpath in sorted(rules_dir.glob("*.json")):
                try:
                    raw_list = json.loads(jpath.read_text(encoding="utf-8"))
                    items = raw_list if isinstance(raw_list, list) else raw_list.get("rules", [])
                    for item in items:
                        parsed = cls._rule_from_dict(item)
                        if parsed:
                            rules_by_id[parsed.id] = parsed
                except Exception:
                    continue

        return list(rules_by_id.values())

    @classmethod
    def _refresh_from_feed(
        cls, db: IndexerDB, feed_url: str, now: float, fetcher: Any = None
    ) -> None:
        try:
            if fetcher is not None:
                payload = fetcher(feed_url)
            else:
                req = urllib.request.Request(feed_url, headers={"User-Agent": "VibeTrace/1.0"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:  # nosec
                    payload = json.loads(resp.read().decode("utf-8"))
            items = payload if isinstance(payload, list) else payload.get("rules", [])
            with db._get_conn() as conn:
                for item in items:
                    parsed = cls._rule_from_dict(item)
                    if parsed:
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO security_rules_cache (
                                rule_id, rule_json, updated_at
                            ) VALUES (?, ?, ?)
                            """,
                            (parsed.id, json.dumps(item), now),
                        )
                conn.commit()
        except Exception:
            # Silent offline fallback: rely on built-in and previously cached SQLite rules
            return

    @staticmethod
    def _rule_from_dict(data: dict[str, Any]) -> DeclarativeRule | None:
        rid = str(data.get("id", "")).strip()
        cwe = str(data.get("cwe_id", "")).strip()
        pat = str(data.get("pattern", "")).strip()
        if not rid or not cwe or not pat:
            return None
        sev_str = str(data.get("severity", "MEDIUM")).upper()
        sev = Severity(sev_str) if sev_str in Severity.__members__ else Severity.MEDIUM
        langs = tuple(str(x).lstrip(".") for x in data.get("languages", ["py", "js", "ts", "tsx"]))
        return DeclarativeRule(
            id=rid,
            cwe_id=cwe,
            severity=sev,
            title=str(data.get("title", f"{cwe} Security Rule")),
            message=str(data.get("message", "Security policy violation detected.")),
            pattern=pat,
            languages=langs,
            compliant_snippet=str(data.get("compliant_snippet", "")),
        )

    @classmethod
    def scan_file_with_rules(
        cls,
        file_path: str,
        project_root: Path,
        rules: list[DeclarativeRule],
    ) -> list[SecurityFinding]:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        applicable = [r for r in rules if ext in r.languages]
        if not applicable:
            return []
        abs_path = (project_root / file_path).resolve()
        try:
            lines = abs_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []

        findings: list[SecurityFinding] = []
        for line_idx, line in enumerate(lines, 1):
            stripped = line.strip()
            if not stripped or stripped.startswith(
                (
                    "#",
                    "//",
                    "/*",
                    "*",
                    '"',
                    "'",
                    'r"',
                    "r'",
                    "pattern=",
                    "non_compliant_example=",
                    "compliant_example=",
                    "message=",
                    "explanation=",
                )
            ):
                continue
            for rule in applicable:
                if not re.search(rule.pattern, line):
                    continue
                nosec = _NOSEC_RE.search(line)
                if nosec and not (nosec.group(1) or nosec.group(2)):
                    continue
                code_hash = hashlib.sha256(stripped.encode("utf-8")).hexdigest()
                spec = RULES_CATALOG.get(rule.cwe_id)
                f = SecurityFinding(
                    id=f"SEC-{rule.cwe_id}-{code_hash[:12]}",
                    rule_id=rule.id,
                    cwe_id=rule.cwe_id,
                    category=FindingCategory.AST_PATTERN,
                    severity=rule.severity,
                    title=rule.title,
                    message=rule.message,
                    file_path=file_path,
                    start_line=line_idx,
                    end_line=line_idx,
                    code_hash=code_hash,
                    non_compliant_snippet=stripped,
                    compliant_snippet=rule.compliant_snippet
                    or (spec.compliant_example if spec else None),
                    remediation_guidance=spec.explanation if spec else rule.message,
                )
                if nosec and (nosec.group(1) or nosec.group(2)):
                    f.status = "APPROVED"
                    f.tag = (nosec.group(1) or "inline-ignore").lower()
                    f.rationale = (nosec.group(2) or "Approved via inline tag").strip()
                    f.reviewed_by = "inline-tag"
                findings.append(f)
        return findings
