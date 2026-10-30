"""Self-updating declarative & Semgrep-compatible security rule catalog with SQLite TTL caching."""

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
    "https://raw.githubusercontent.com/serguei9090/VibeTrace/main/rules/security_catalog.json"
)
_NOSEC_RE = re.compile(
    r"(?:#|//)\s*(?:nosec|vibetrace:ignore)(?:\s*\[([a-zA-Z0-9_-]+)\])?(?:\s*:\s*(.+))?$",
    re.IGNORECASE,
)
_LANG_ALIAS = {
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
}


@dataclass(frozen=True)
class DeclarativeRule:
    """Declarative pattern rule for polyglot OWASP / CWE / Semgrep checks."""

    id: str
    cwe_id: str
    severity: Severity
    title: str
    message: str
    pattern: str
    languages: tuple[str, ...]
    compliant_snippet: str = ""
    negative_pattern: str = ""


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


def parse_semgrep_yaml(yaml_text: str) -> list[dict[str, Any]]:
    """Parses Semgrep YAML rule definitions without requiring external YAML binaries."""
    try:
        import yaml  # type: ignore[import-untyped]

        loaded = yaml.safe_load(yaml_text)
        if isinstance(loaded, dict) and isinstance(loaded.get("rules"), list):
            return loaded["rules"]
    except Exception:
        pass

    rules: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    in_list_key: str | None = None

    for raw_line in yaml_text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "rules:":
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if stripped.startswith("- id:") or (indent <= 4 and stripped.startswith("- ")):
            if stripped.startswith("- id:"):
                if current:
                    rules.append(current)
                current = {"id": stripped.split(":", 1)[1].strip().strip("'\"")}
                in_list_key = None
                continue
        if current is None:
            continue
        if stripped.startswith("- ") and in_list_key:
            val = stripped[2:].strip().strip("'\"")
            if val.startswith("pattern:"):
                val = val.split(":", 1)[1].strip().strip("'\"")
            current.setdefault(in_list_key, []).append(val)
            continue
        if ":" in stripped:
            k, v = stripped.split(":", 1)
            key = k.lstrip("- ").strip()
            val = v.strip().strip("'\"")
            if not val:
                in_list_key = key if key in ("languages", "pattern-either", "patterns") else None
                continue
            in_list_key = None
            if val.startswith("[") and val.endswith("]"):
                current[key] = [x.strip().strip("'\"") for x in val[1:-1].split(",") if x.strip()]
            else:
                current[key] = val

    if current:
        rules.append(current)
    return rules


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
    DeclarativeRule(
        id="web:CWE-601-open-redirect",
        cwe_id="CWE-601",
        severity=Severity.MEDIUM,
        title="URL Redirection to Untrusted Site ('Open Redirect')",
        message="Direct redirect to unvalidated request parameter or query string.",
        pattern=r"\b(?:res\.)?redirect\s*\(\s*(?:request\.(?:args|query|GET|POST)|req\.(?:query|body))",
        languages=("py", "js", "ts", "tsx"),
        compliant_snippet="return redirect(url_for('dashboard'))",
    ),
    DeclarativeRule(
        id="db:CWE-943-nosql-injection",
        cwe_id="CWE-943",
        severity=Severity.CRITICAL,
        title="Improper Neutralization of Special Elements in Data Query Logic ('NoSQL Injection')",
        message="MongoDB '$where' operator executes arbitrary JavaScript on query evaluation.",
        pattern=r"['\"]\$where['\"]\s*:",
        languages=("py", "js", "ts", "tsx"),
        compliant_snippet="db.users.find({'username': str(user)})",
    ),
    DeclarativeRule(
        id="js:CWE-1321-prototype-pollution",
        cwe_id="CWE-1321",
        severity=Severity.HIGH,
        title="Improperly Controlled Modification of Object Prototype ('Prototype Pollution')",
        message="Direct mutation of '__proto__' or 'constructor.prototype'.",
        pattern=r"(?:\.__proto__\s*(?:\[|=)|\[\s*['\"]__proto__['\"]\s*\]\s*=|constructor\.prototype\s*\[)",
        languages=("js", "ts", "tsx", "jsx"),
        compliant_snippet="const safeMap = Object.create(null);",
    ),
    DeclarativeRule(
        id="web:CWE-1336-ssti",
        cwe_id="CWE-1336",
        severity=Severity.CRITICAL,
        title="Server-Side Template Injection ('SSTI')",
        message="Dynamic template rendering via 'render_template_string' or 'jinja2.Template'.",
        pattern=r"\b(?:render_template_string|jinja2\.Template)\s*\(\s*(?:f['\"]|request\.|[a-zA-Z_][a-zA-Z0-9_]*\s*\+)",
        languages=("py",),
        compliant_snippet="render_template('page.html', value=user_value)",
    ),
    DeclarativeRule(
        id="crypto:CWE-330-weak-random",
        cwe_id="CWE-330",
        severity=Severity.MEDIUM,
        title="Use of Insufficiently Random Values",
        message="Non-cryptographic PRNG ('random.*' / 'Math.random') used for secret/token/OTP.",
        pattern=(
            r"\b\w*(?:token|secret|otp|nonce|api_key|session_id|reset_code)\w*\s*=\s*"
            r"[^#\n]*(?:\brandom\.(?:randint|choice|random|randrange)|Math\.random\s*\()"
        ),
        languages=("py", "js", "ts", "tsx"),
        compliant_snippet="reset_token = secrets.token_urlsafe(32)",
    ),
    DeclarativeRule(
        id="fs:CWE-377-insecure-temp",
        cwe_id="CWE-377",
        severity=Severity.MEDIUM,
        title="Insecure Temporary File",
        message="'tempfile.mktemp' is vulnerable to symlink race conditions (TOCTOU).",
        pattern=r"\btempfile\.mktemp\s*\(",
        languages=("py",),
        compliant_snippet="with tempfile.NamedTemporaryFile(delete=False) as tmp: ...",
    ),
    DeclarativeRule(
        id="crypto:CWE-321-hardcoded-pem-key",
        cwe_id="CWE-321",
        severity=Severity.CRITICAL,
        title="Use of Hard-coded Cryptographic Key",
        message="Hardcoded PEM private key block detected in source code.",
        pattern=r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
        languages=("py", "js", "ts", "tsx", "go", "rs"),
        compliant_snippet="PRIVATE_KEY = os.environ['SIGNING_PRIVATE_KEY']",
    ),
    DeclarativeRule(
        id="web:CWE-942-cors-wildcard-credentials",
        cwe_id="CWE-942",
        severity=Severity.MEDIUM,
        title="Permissive Cross-domain Policy with Untrusted Domains ('Insecure CORS')",
        message="Wildcard 'allow_origins=[\"*\"]' combined with 'allow_credentials=True'.",
        pattern=r"allow_origins\s*=\s*\[\s*['\"]\*['\"]\s*\][^#\n]*allow_credentials\s*=\s*True",
        languages=("py",),
        compliant_snippet="CORSMiddleware(app, allow_origins=['https://app.example.com'])",
    ),
    DeclarativeRule(
        id="web:CWE-489-debug-enabled",
        cwe_id="CWE-489",
        severity=Severity.MEDIUM,
        title="Active Debug Code in Production",
        message="Web application launched with 'debug=True'.",
        pattern=r"\bapp\.run\s*\([^)]*\bdebug\s*=\s*True\b",
        languages=("py",),
        compliant_snippet="app.run(host='0.0.0.0', debug=False)",
    ),
    DeclarativeRule(
        id="sec:CWE-798-token-prefix",
        cwe_id="CWE-798",
        severity=Severity.CRITICAL,
        title="Use of Hard-coded Credentials (Leaked Cloud/API Token)",
        message="Hardcoded AWS, GitHub, Stripe, or Slack secret token literal detected.",
        pattern=r"['\"](?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36}|sk_live_[0-9a-zA-Z]{24})['\"]",
        languages=("py", "js", "ts", "tsx", "go", "rs"),
        compliant_snippet="API_TOKEN = os.environ['SERVICE_API_TOKEN']",
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

        # Merge local custom project rules (.vibetrace/rules/*.json, *.yaml, *.yml)
        rules_dir = project_root / ".vibetrace" / "rules"
        if rules_dir.is_dir():
            for rpath in sorted(rules_dir.iterdir()):
                if rpath.suffix.lower() not in (".json", ".yaml", ".yml"):
                    continue
                try:
                    text = rpath.read_text(encoding="utf-8")
                    if rpath.suffix.lower() == ".json":
                        raw = json.loads(text)
                        items = raw if isinstance(raw, list) else raw.get("rules", [])
                    else:
                        items = parse_semgrep_yaml(text)
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
            return

    @staticmethod
    def _rule_from_dict(data: dict[str, Any]) -> DeclarativeRule | None:
        """Converts a VibeTrace JSON rule or Semgrep YAML/JSON rule into DeclarativeRule."""
        rid = str(data.get("id", "")).strip()
        if not rid:
            return None

        # Extract CWE from top-level `cwe_id`, `cwe`, or `metadata.cwe` (Semgrep format)
        cwe_raw = data.get("cwe_id") or data.get("cwe") or (data.get("metadata") or {}).get("cwe")
        if isinstance(cwe_raw, list) and cwe_raw:
            cwe_raw = cwe_raw[0]
        cwe_match = re.search(r"CWE-\d+", str(cwe_raw or ""), re.IGNORECASE)
        cwe = cwe_match.group(0).upper() if cwe_match else str(cwe_raw or "CWE-710").strip()

        # Extract regex pattern or compile Semgrep `pattern` / `pattern-either` / `pattern-regex`
        pat = str(data.get("pattern-regex", "")).strip()
        if not pat:
            raw_pat = str(data.get("pattern", "")).strip()
            if raw_pat:
                pat = (
                    compile_semgrep_pattern(raw_pat)
                    if ("$" in raw_pat or "..." in raw_pat)
                    else raw_pat
                )
            elif isinstance(data.get("pattern-either"), list):
                sub_pats = []
                for entry in data["pattern-either"]:
                    sp = entry.get("pattern", "") if isinstance(entry, dict) else str(entry)
                    if sp:
                        sub_pats.append(compile_semgrep_pattern(str(sp)))
                if sub_pats:
                    pat = "(?:" + "|".join(sub_pats) + ")"
        if not pat:
            return None

        neg_pat = str(data.get("pattern-not-regex", "")).strip()
        if not neg_pat and data.get("pattern-not"):
            neg_pat = compile_semgrep_pattern(str(data["pattern-not"]))

        # Map Semgrep ERROR/WARNING/INFO or CRITICAL/HIGH/MEDIUM/LOW
        sev_str = str(data.get("severity", "MEDIUM")).upper()
        sev_map = {"ERROR": Severity.HIGH, "WARNING": Severity.MEDIUM, "INFO": Severity.LOW}
        sev = sev_map.get(
            sev_str, Severity[sev_str] if sev_str in Severity.__members__ else Severity.MEDIUM
        )

        # Normalize language names (`python` -> `py`, `typescript` -> `ts`, `tsx`)
        raw_langs = data.get("languages", ["py", "js", "ts", "tsx"])
        langs: list[str] = []
        for lang in raw_langs:
            key = str(lang).lower().lstrip(".")
            for ext in _LANG_ALIAS.get(key, (key,)):
                if ext not in langs:
                    langs.append(ext)

        return DeclarativeRule(
            id=rid,
            cwe_id=cwe,
            severity=sev,
            title=str(data.get("title", f"{cwe} Security Rule ({rid})")),
            message=str(data.get("message", "Security policy violation detected.")),
            pattern=pat,
            languages=tuple(langs),
            compliant_snippet=str(data.get("compliant_snippet") or data.get("fix") or ""),
            negative_pattern=neg_pat,
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
                if rule.negative_pattern and re.search(rule.negative_pattern, line):
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
