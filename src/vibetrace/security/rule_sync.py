"""Self-updating declarative & Semgrep-compatible security rule catalog with SQLite TTL caching."""

from __future__ import annotations

import hashlib
import json
import re
import time
import tomllib
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
from vibetrace.security.rules import RULES_CATALOG

DEFAULT_RULES_TTL_DAYS = 7
PINNED_CATALOG_REF = "v0.1.8"
DEFAULT_RULES_FEED_URL = (
    f"https://raw.githubusercontent.com/serguei9090/VibeTrace/{PINNED_CATALOG_REF}/"
    "rules/security_catalog.json"
)
BUNDLED_SECURITY_CATALOG_PATH = Path(__file__).parent.parent / "resources" / "security_catalog.json"
EXPECTED_FEED_SHA256: dict[str, str] = {
    DEFAULT_RULES_FEED_URL: "7933de55c47db5534274e3bf62ac2ffec7146c273195a68cc8e466def27c35ae",
}
# Official public Semgrep Community Registry rule feeds pinned to immutable tag
_SEMGREP_PINNED_REF = "v1.69.0"
TRUSTED_COMMUNITY_YAML_FEEDS: dict[str, tuple[str, ...]] = {
    "py": (
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/python/lang/security/audit/dangerous-system-call.yaml",
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/python/lang/security/deserialization/pickle.yaml",
    ),
    "js": (
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/javascript/lang/security/detect-eval-with-expression.yaml",
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/javascript/lang/security/audit/unsafe-dynamic-method.yaml",
    ),
    "ts": (
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/javascript/lang/security/detect-eval-with-expression.yaml",
    ),
    "go": (
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/go/lang/security/audit/crypto/math_random.yaml",
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/go/lang/security/audit/net/use-tls.yaml",
    ),
    "rs": (
        f"https://raw.githubusercontent.com/semgrep/semgrep-rules/{_SEMGREP_PINNED_REF}/rust/lang/security/unsafe-usage.yaml",
    ),
}
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
    patterns_not: tuple[str, ...] = field(default_factory=tuple)
    pattern_inside: str = ""


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
            if in_list_key == "patterns" and ":" in val:
                k, v = val.split(":", 1)
                current.setdefault(in_list_key, []).append({k.strip(): v.strip().strip("'\"")})
            else:
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
    # --- Expanded Community Security Pack (Python, JS/TS, Go, Rust) ---
    DeclarativeRule(
        id="py:CWE-22-tarfile-extractall",
        cwe_id="CWE-22",
        severity=Severity.HIGH,
        title="Arbitrary File Overwrite via Archive Extraction ('ZipSlip')",
        message="'tarfile.extractall()' without 'filter=\"data\"' allows path traversal.",
        pattern=r"\.extractall\s*\(\s*\)",
        languages=("py",),
        compliant_snippet="tar.extractall(path=dest, filter='data')",
    ),
    DeclarativeRule(
        id="py:CWE-502-torch-load-unsafe",
        cwe_id="CWE-502",
        severity=Severity.CRITICAL,
        title="Unsafe PyTorch Model Deserialization",
        message="'torch.load()' without 'weights_only=True' uses pickle and can execute code.",
        pattern=r"\btorch\.load\s*\([^)]*\)",
        negative_pattern=r"weights_only\s*=\s*True",
        languages=("py",),
        compliant_snippet="torch.load(model_path, weights_only=True)",
    ),
    DeclarativeRule(
        id="py:CWE-352-csrf-exempt",
        cwe_id="CWE-352",
        severity=Severity.MEDIUM,
        title="Cross-Site Request Forgery (CSRF) Protection Disabled",
        message="Disabling CSRF verification via '@csrf_exempt' exposes state-changing endpoints.",
        pattern=r"@csrf_exempt\b",
        languages=("py",),
        compliant_snippet="# Keep CSRF middleware active for cookie-authenticated endpoints",
    ),
    DeclarativeRule(
        id="js:CWE-95-new-function",
        cwe_id="CWE-95",
        severity=Severity.HIGH,
        title="Dynamic Code Evaluation via 'new Function()'",
        message="Constructing functions from strings via 'new Function()' is equivalent to eval().",
        pattern=r"\bnew\s+Function\s*\(",
        languages=("js", "ts", "tsx", "jsx"),
        compliant_snippet="// Use a safe expression parser or predefined callback map",
    ),
    DeclarativeRule(
        id="js:CWE-345-postmessage-wildcard",
        cwe_id="CWE-345",
        severity=Severity.MEDIUM,
        title="Cross-Origin 'postMessage' with Wildcard TargetOrigin ('*')",
        message="Sending messages with targetOrigin '*' leaks data to any embedding origin.",
        pattern=r"\.postMessage\s*\([^,]+,\s*['\"]\*['\"]\s*\)",
        languages=("js", "ts", "tsx", "jsx"),
        compliant_snippet="window.postMessage(payload, 'https://trusted.example.com')",
    ),
    DeclarativeRule(
        id="js:CWE-1004-cookie-httponly-false",
        cwe_id="CWE-1004",
        severity=Severity.MEDIUM,
        title="Sensitive Cookie Without 'HttpOnly' Flag",
        message="Setting 'httpOnly: false' allows client-side scripts (XSS) to steal cookies.",
        pattern=r"\bhttpOnly\s*:\s*false\b",
        languages=("js", "ts", "tsx", "jsx"),
        compliant_snippet="res.cookie('session', token, { httpOnly: true, secure: true })",
    ),
    DeclarativeRule(
        id="js:CWE-200-next-public-secret",
        cwe_id="CWE-200",
        severity=Severity.CRITICAL,
        title="Server Secret Exposed to Client Bundle via 'NEXT_PUBLIC_'",
        message=(
            "Referencing 'NEXT_PUBLIC_*_SECRET' or 'NEXT_PUBLIC_*_PRIVATE_KEY' "
            "bundles secrets to browsers."
        ),
        pattern=r"NEXT_PUBLIC_[A-Z0-9_]*(?:SECRET|PRIVATE_KEY|PASSWORD|DATABASE_URL)",
        languages=("js", "ts", "tsx", "jsx"),
        compliant_snippet="process.env.SERVER_ONLY_SECRET",
    ),
    DeclarativeRule(
        id="go:CWE-295-tls-insecure-skip-verify",
        cwe_id="CWE-295",
        severity=Severity.HIGH,
        title="Go TLS 'InsecureSkipVerify: true' Disables Certificate Validation",
        message="Setting 'InsecureSkipVerify: true' exposes TLS connections to MITM attacks.",
        pattern=r"\bInsecureSkipVerify\s*:\s*true\b",
        languages=("go",),
        compliant_snippet="&tls.Config{MinVersion: tls.VersionTLS13}",
    ),
    DeclarativeRule(
        id="go:CWE-400-http-listenandserve-no-timeout",
        cwe_id="CWE-400",
        severity=Severity.MEDIUM,
        title="Go 'http.ListenAndServe' Without Timeouts ('Slowloris DoS')",
        message="Default 'http.ListenAndServe' has no ReadHeaderTimeout, enabling Slowloris DoS.",
        pattern=r"\bhttp\.ListenAndServe(?:TLS)?\s*\(",
        languages=("go",),
        compliant_snippet="srv := &http.Server{Addr: addr, ReadHeaderTimeout: 5 * time.Second}",
    ),
    DeclarativeRule(
        id="rs:CWE-295-reqwest-accept-invalid-certs",
        cwe_id="CWE-295",
        severity=Severity.HIGH,
        title="Rust TLS 'danger_accept_invalid_certs(true)' Enabled",
        message="Accepting invalid TLS certificates disables authenticity checks.",
        pattern=r"\.danger_accept_invalid_certs\s*\(\s*true\s*\)",
        languages=("rs",),
        compliant_snippet="reqwest::Client::builder().use_rustls_tls().build()",
    ),
    DeclarativeRule(
        id="rs:CWE-843-mem-transmute",
        cwe_id="CWE-843",
        severity=Severity.HIGH,
        title="Unsafe Type Reinterpretation via 'std::mem::transmute'",
        message="'mem::transmute' bypasses Rust's type system and can cause undefined behavior.",
        pattern=r"\bmem::transmute\b",
        languages=("rs",),
        compliant_snippet="// Use TryFrom / From or safe byte conversion methods (to_le_bytes)",
    ),
]


def _is_enclosed_by(lines: list[str], line_idx: int, pattern: str) -> bool:
    """Checks if the line at line_idx (1-based) is enclosed by a scope matching pattern."""
    if not pattern:
        return True
    curr_line = lines[line_idx - 1]
    curr_indent = len(curr_line) - len(curr_line.lstrip())
    for prev_idx in range(line_idx - 2, -1, -1):
        prev = lines[prev_idx]
        if not prev.strip():
            continue
        p_indent = len(prev) - len(prev.lstrip())
        if p_indent < curr_indent:
            if re.search(pattern, prev):
                return True
            curr_indent = p_indent
            if curr_indent == 0:
                break
    return False


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
    def detect_project_languages(cls, db: IndexerDB) -> set[str]:
        """Detects all file extensions present in the indexed project (`py`, `ts`, `go`, etc.)."""
        exts: set[str] = set()
        with db._get_conn() as conn:
            for (fp,) in conn.execute(
                "SELECT DISTINCT file_path FROM nodes WHERE file_path IS NOT NULL"
            ).fetchall():
                if "." in str(fp):
                    ext = str(fp).rsplit(".", 1)[-1].lower()
                    for norm in _LANG_ALIAS.get(ext, (ext,)):
                        exts.add(norm)
        return exts

    @classmethod
    def sync_and_load_rules(
        cls,
        db: IndexerDB,
        project_root: Path,
        force_update: bool = False,
        fetcher: Any = None,
    ) -> list[DeclarativeRule]:
        """Loads built-in rules, auto-syncs remote catalog on TTL expiry, and filters languages."""
        cls._ensure_table(db)
        project_langs = cls.detect_project_languages(db)
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
            cls._refresh_from_feed(db, feed_url, now, fetcher=fetcher, project_langs=project_langs)
        elif oldest_ts == 0.0:
            # One-time initial seed of SQLite cache from built-in language-matched rules
            cls._seed_builtin_cache(db, now, project_langs)

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

        all_rules = list(rules_by_id.values())
        if not project_langs:
            return all_rules
        return [r for r in all_rules if set(r.languages) & project_langs]

    @classmethod
    def _seed_builtin_cache(cls, db: IndexerDB, now: float, project_langs: set[str]) -> None:
        bundled_items: list[dict[str, Any]] = []
        if BUNDLED_SECURITY_CATALOG_PATH.is_file():
            try:
                raw_json = json.loads(
                    BUNDLED_SECURITY_CATALOG_PATH.read_text(encoding="utf-8", errors="replace")
                )
                rules_list = raw_json if isinstance(raw_json, list) else raw_json.get("rules", [])
                if isinstance(rules_list, list):
                    bundled_items.extend(rules_list)
            except Exception:
                pass

        with db._get_conn() as conn:
            for r in BUILTIN_DECLARATIVE_RULES:
                if project_langs and not (set(r.languages) & project_langs):
                    continue
                item = {
                    "id": r.id,
                    "cwe_id": r.cwe_id,
                    "severity": r.severity.value,
                    "title": r.title,
                    "message": r.message,
                    "pattern-regex": r.pattern,
                    "languages": list(r.languages),
                    "compliant_snippet": r.compliant_snippet,
                }
                conn.execute(
                    """
                    INSERT OR IGNORE INTO security_rules_cache (rule_id, rule_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (r.id, json.dumps(item), now),
                )
            for item in bundled_items:
                parsed = cls._rule_from_dict(item)
                if not parsed:
                    continue
                if project_langs and not (set(parsed.languages) & project_langs):
                    continue
                conn.execute(
                    """
                    INSERT OR IGNORE INTO security_rules_cache (rule_id, rule_json, updated_at)
                    VALUES (?, ?, ?)
                    """,
                    (parsed.id, json.dumps(item), now),
                )
            conn.commit()

    @classmethod
    def _refresh_from_feed(
        cls,
        db: IndexerDB,
        feed_url: str,
        now: float,
        fetcher: Any = None,
        project_langs: set[str] | None = None,
        expected_sha256: str | None = None,
    ) -> None:
        items: list[dict[str, Any]] = []
        try:
            if fetcher is not None:
                payload = fetcher(feed_url)
            else:
                req = urllib.request.Request(feed_url, headers={"User-Agent": "VibeTrace/1.0"})
                with urllib.request.urlopen(req, timeout=2.5) as resp:  # nosec
                    raw_bytes = resp.read()
                    pin_hash = expected_sha256 or EXPECTED_FEED_SHA256.get(feed_url)
                    if pin_hash:
                        normalized = raw_bytes.replace(b"\r\n", b"\n")
                        actual_raw = hashlib.sha256(raw_bytes).hexdigest()
                        actual_norm = hashlib.sha256(normalized).hexdigest()
                        if pin_hash.lower() not in (actual_raw, actual_norm):
                            raise ValueError(
                                f"Supply-chain SHA-256 mismatch for {feed_url}: "
                                f"expected {pin_hash}, got {actual_raw}"
                            )
                    raw_text = raw_bytes.decode("utf-8")
                    payload = (
                        parse_semgrep_yaml(raw_text)
                        if feed_url.endswith((".yaml", ".yml"))
                        else json.loads(raw_text)
                    )
            loaded = payload if isinstance(payload, list) else payload.get("rules", [])
            if isinstance(loaded, list):
                items.extend(loaded)
        except Exception:
            pass

        # Pull official public Semgrep community YAML feeds for detected project languages
        if fetcher is None and project_langs:
            seen_urls: set[str] = set()
            for lang in sorted(project_langs):
                for comm_url in TRUSTED_COMMUNITY_YAML_FEEDS.get(lang, ()):
                    if comm_url in seen_urls:
                        continue
                    seen_urls.add(comm_url)
                    try:
                        req = urllib.request.Request(
                            comm_url, headers={"User-Agent": "VibeTrace/1.0"}
                        )
                        with urllib.request.urlopen(req, timeout=2.0) as resp:  # nosec
                            yaml_rules = parse_semgrep_yaml(resp.read().decode("utf-8"))
                            items.extend(yaml_rules)
                    except Exception:
                        continue

        if not items:
            return

        with db._get_conn() as conn:
            for item in items:
                parsed = cls._rule_from_dict(item)
                if not parsed:
                    continue
                if project_langs and not (set(parsed.languages) & project_langs):
                    continue
                conn.execute(
                    """
                    INSERT OR REPLACE INTO security_rules_cache (
                        rule_id, rule_json, updated_at
                    ) VALUES (?, ?, ?)
                    """,
                    (parsed.id, json.dumps(item), now),
                )
            conn.commit()

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
        patterns_not: list[str] = []
        pattern_inside = ""

        if not pat:
            raw_pat = str(data.get("pattern", "")).strip()
            if raw_pat:
                is_semgrep_pat = bool(
                    re.search(r"(?<!\\)\$[A-Z_][A-Z0-9_]*", raw_pat) or "..." in raw_pat
                )
                pat = compile_semgrep_pattern(raw_pat) if is_semgrep_pat else raw_pat
            elif isinstance(data.get("pattern-either"), list):
                sub_pats = []
                for entry in data["pattern-either"]:
                    sp = entry.get("pattern", "") if isinstance(entry, dict) else str(entry)
                    if sp:
                        sub_pats.append(compile_semgrep_pattern(str(sp)))
                if sub_pats:
                    pat = "(?:" + "|".join(sub_pats) + ")"
            elif isinstance(data.get("patterns"), list):
                for sub in data["patterns"]:
                    if isinstance(sub, dict):
                        if sub.get("pattern") and not pat:
                            pat = compile_semgrep_pattern(str(sub["pattern"]))
                        if sub.get("pattern-not"):
                            patterns_not.append(compile_semgrep_pattern(str(sub["pattern-not"])))
                        if sub.get("pattern-inside") and not pattern_inside:
                            pattern_inside = compile_semgrep_pattern(str(sub["pattern-inside"]))
                    elif isinstance(sub, str) and not pat:
                        pat = compile_semgrep_pattern(sub)
        if not pat:
            return None

        # Handle top-level pattern-not (list or string)
        raw_not = data.get("pattern-not-regex") or data.get("pattern-not")
        if isinstance(raw_not, list):
            for item in raw_not:
                if str(item).strip():
                    patterns_not.append(compile_semgrep_pattern(str(item).strip()))
        elif isinstance(raw_not, str) and raw_not.strip():
            patterns_not.append(compile_semgrep_pattern(raw_not.strip()))

        # Handle top-level pattern-inside
        raw_inside = data.get("pattern-inside")
        if isinstance(raw_inside, str) and raw_inside.strip() and not pattern_inside:
            pattern_inside = compile_semgrep_pattern(raw_inside.strip())

        neg_pat = str(data.get("pattern-not-regex", "")).strip()
        if not neg_pat and data.get("pattern-not") and isinstance(data["pattern-not"], str):
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
            patterns_not=tuple(patterns_not),
            pattern_inside=pattern_inside,
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
            file_text = abs_path.read_text(encoding="utf-8", errors="replace")
            lines = file_text.splitlines()
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
                if rule.pattern_inside and not _is_enclosed_by(
                    lines, line_idx, rule.pattern_inside
                ):
                    continue
                if not re.search(rule.pattern, line):
                    continue
                if rule.negative_pattern and re.search(rule.negative_pattern, line):
                    continue
                if any(re.search(pn, line) for pn in rule.patterns_not):
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
