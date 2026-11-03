"""Gitleaks-grade Secret, DB URI & Shannon Entropy Scanner across Git history and .env."""

from __future__ import annotations

import hashlib
import math
import re
import subprocess
from collections import Counter
from pathlib import Path

from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

# 25+ Gitleaks-compatible cloud, AI, SaaS, and package registry token signatures
_KNOWN_TOKEN_RE = re.compile(
    r"("
    r"AKIA[0-9A-Z]{16}"  # AWS Access Key ID
    r"|ASIA[0-9A-Z]{16}"  # AWS STS Temporary Key
    r"|gh[pousr]_[A-Za-z0-9_]{36,}"  # GitHub Personal / OAuth / App Token
    r"|github_pat_[A-Za-z0-9_]{22,}"  # GitHub Fine-Grained PAT
    r"|glpat-[A-Za-z0-9_-]{20,}"  # GitLab Personal Access Token
    r"|sk-(?:live|test|proj|ant)-[A-Za-z0-9_-]{20,}"  # Stripe / OpenAI / Anthropic Key
    r"|hf_[A-Za-z0-9]{34,}"  # HuggingFace API Token
    r"|gsk_[A-Za-z0-9]{40,}"  # Groq API Key
    r"|r8_[A-Za-z0-9]{36,}"  # Replicate API Token
    r"|xox[baprs]-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24,}"  # Slack Token
    r"|https://hooks\.slack\.com/services/T[A-Z0-9]{8,}/"
    r"B[A-Z0-9]{8,}/[A-Za-z0-9]{24,}"  # Slack Webhook
    r"|https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/"
    r"[0-9]{17,20}/[A-Za-z0-9_-]{60,}"  # Discord Webhook
    r"|AIza[0-9A-Za-z_-]{35}"  # Google Cloud / Firebase API Key
    r"|SG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}"  # SendGrid API Token
    r"|SK[0-9a-f]{32}"  # Twilio API Key
    r"|key-[0-9a-zA-Z]{32}"  # Mailgun API Key
    r"|dop_v1_[a-f0-9]{64}"  # DigitalOcean PAT
    r"|doo_v1_[a-f0-9]{64}"  # DigitalOcean OAuth Token
    r"|npm_[A-Za-z0-9]{36}"  # npm Access Token
    r"|pypi-AgEIcHlwaS5vcmc[A-Za-z0-9_-]{50,}"  # PyPI Upload Token
    r"|dapi[0-9a-f]{32}"  # Databricks Access Token
    r"|mistral-[a-zA-Z0-9]{32}"  # Mistral API Token
    r"|pplx-[a-f0-9]{48}"  # Perplexity API Key
    r"|co-[a-zA-Z0-9]{40}"  # Cohere API Key
    r"|tskey-auth-[a-zA-Z0-9_-]{20,}"  # Tailscale Auth Key
    r"|PMAK-[a-f0-9]{24}-[a-f0-9]{34}"  # Postman API Key
    r"|eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9\.[A-Za-z0-9_-]{50,}\.[A-Za-z0-9_-]{40,}"  # Supabase JWT
    r"|-----BEGIN (?:RSA |EC |OPENSSH |DSA |PRIVATE )?PRIVATE KEY-----"  # PEM Private Key
    r")"
)

# Database & Message Broker Connection URIs containing embedded non-placeholder passwords
_DB_URI_SECRET_RE = re.compile(
    r"\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis(?:s)?|amqp(?:s)?)"
    r"://[^:\s'\"]*:([^@\s'\"]{4,})@[^\s'\"]+)",
    re.I,
)

_HIGH_ENTROPY_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret[_-]?key|client[_-]?secret|auth[_-]?token|"
    r"access[_-]?token|private[_-]?key|password|passwd|api[_-]?secret|jwt[_-]?secret)\b"
    r"\s*[:=]\s*['\"]([A-Za-z0-9+/=_-]{20,})['\"]"
)

_SAFE_PLACEHOLDERS = {
    "your_api_key",
    "your-api-key",
    "changeme",
    "placeholder",
    "example",
    "dummy",
    "secret",
    "password",
    "pass",
    "admin",
    "root",
    "postgres",
    "user",
    "none",
    "null",
    "undefined",
    "localhost",
}


def shannon_entropy(data: str) -> float:
    """Calculates Shannon entropy in bits per character."""
    if not data:
        return 0.0
    length = len(data)
    counts = Counter(data)
    return -sum((c / length) * math.log2(c / length) for c in counts.values())


def _mask_secret(raw: str) -> str:
    """Masks the middle of a detected secret so reports never leak raw credentials."""
    if len(raw) <= 10:
        return raw[:3] + "***"
    return f"{raw[:6]}...{raw[-4:]}"


def _load_gitleaksignore(project_root: Path) -> set[str]:
    """Loads ignored paths or fingerprints from .gitleaksignore if present."""
    ignore_path = project_root / ".gitleaksignore"
    if not ignore_path.is_file():
        return set()
    try:
        return {
            ln.strip()
            for ln in ignore_path.read_text(encoding="utf-8", errors="replace").splitlines()
            if ln.strip() and not ln.strip().startswith("#")
        }
    except OSError:
        return set()


class GitSecretScanner:
    """Scans tracked .env files, git working/staged diffs, and commit history for leaked secrets."""

    @classmethod
    def scan_repository(
        cls,
        project_root: Path,
        commit_depth: int = 10,
        include_tests: bool = False,
    ) -> list[SecurityFinding]:
        """Scans tracked .env files, uncommitted diff, and recent N commits."""
        findings: list[SecurityFinding] = []
        seen_ids: set[str] = set()
        gitleaks_ignored = _load_gitleaksignore(project_root)

        # 1. Check if any .env file (other than .env.example / .env.sample) is tracked
        for env_f in cls._scan_tracked_env_files(project_root):
            if env_f.id not in seen_ids and env_f.file_path not in gitleaks_ignored:
                seen_ids.add(env_f.id)
                findings.append(env_f)

        # 2. Scan uncommitted working tree + staged diff + recent N commits
        patches = cls._collect_git_patches(project_root, commit_depth=commit_depth)
        for source_label, patch_text in patches:
            for f in cls._scan_patch_text(patch_text, source_label, include_tests=include_tests):
                if (
                    f.id not in seen_ids
                    and f.file_path not in gitleaks_ignored
                    and f"{f.file_path}:{f.rule_id}:{f.start_line}" not in gitleaks_ignored
                ):
                    seen_ids.add(f.id)
                    findings.append(f)

        return findings

    @classmethod
    def scan_staged(cls, project_root: Path) -> list[SecurityFinding]:
        """Scans only git staged files (git diff --cached) for pre-commit blocking."""
        try:
            res = subprocess.run(
                ["git", "diff", "--cached", "-U0", "--no-color"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            patch_text = res.stdout.strip()
            if not patch_text:
                return []
            gitleaks_ignored = _load_gitleaksignore(project_root)
            raw = cls._scan_patch_text(patch_text, "staged", include_tests=False)
            return [
                f
                for f in raw
                if f.file_path not in gitleaks_ignored
                and f"{f.file_path}:{f.rule_id}:{f.start_line}" not in gitleaks_ignored
            ]
        except OSError:
            return []

    @staticmethod
    def _is_ignored_path(file_path: str, include_tests: bool) -> bool:
        norm = file_path.replace("\\", "/")
        parts = norm.lower().split("/")
        fname = parts[-1]
        if fname.endswith((".lock", ".sum", ".svg", ".png", ".jpg")) or fname in (
            ".env.example",
            ".env.sample",
            ".env.template",
            "security_catalog.json",
            "initial_rules.json",
        ):
            return True
        if not include_tests and (
            any(p in ("tests", "test", "e2e", "__tests__", "fixtures") for p in parts[:-1])
            or fname.startswith("test_")
            or fname.endswith(("_test.py", "_test.go", ".spec.ts", ".test.ts"))
        ):
            return True
        return False

    @classmethod
    def _scan_tracked_env_files(cls, project_root: Path) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        try:
            res = subprocess.run(
                ["git", "ls-files", ".env*", "**/.env*"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            for raw_line in res.stdout.splitlines():
                rel = raw_line.strip().replace("\\", "/")
                if not rel:
                    continue
                fname = rel.split("/")[-1].lower()
                if fname in (".env.example", ".env.sample", ".env.template", ".env.test"):
                    continue
                code_hash = hashlib.sha256(f"tracked-env:{rel}".encode()).hexdigest()
                findings.append(
                    SecurityFinding(
                        id=f"SEC-CWE-540-{code_hash[:10]}",
                        rule_id="git:tracked-dotenv-file",
                        cwe_id="CWE-540",
                        category=FindingCategory.GIT_SECRET,
                        severity=Severity.HIGH,
                        title="Environment File (.env) Tracked in Git",
                        message=(
                            f"'{rel}' is tracked by Git and may expose local credentials "
                            "in version history."
                        ),
                        file_path=rel,
                        start_line=1,
                        end_line=1,
                        symbol_name=None,
                        code_hash=code_hash,
                        non_compliant_snippet=f"git add {rel}",
                        compliant_snippet=f"git rm --cached {rel} && echo '{rel}' >> .gitignore",
                        remediation_guidance=(
                            "Untrack '.env' files with 'git rm --cached' and add '.env*' "
                            "to '.gitignore'."
                        ),
                    )
                )
        except Exception:
            pass
        return findings

    @staticmethod
    def _collect_git_patches(project_root: Path, commit_depth: int) -> list[tuple[str, str]]:
        patches: list[tuple[str, str]] = []
        try:
            wt = subprocess.run(
                ["git", "diff", "HEAD", "-U0"],
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if wt.stdout:
                patches.append(("working-tree", wt.stdout))

            if commit_depth > 0:
                hist = subprocess.run(
                    ["git", "log", f"-n{commit_depth}", "-p", "-U0", "--no-color"],
                    cwd=str(project_root),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if hist.stdout:
                    patches.append((f"git-log-last-{commit_depth}", hist.stdout))
        except Exception:
            pass
        return patches

    @classmethod
    def _scan_patch_text(
        cls, patch_text: str, source_label: str, include_tests: bool = False
    ) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        current_commit = source_label
        current_file: str | None = None
        current_line = 1

        for raw_line in patch_text.splitlines():
            if raw_line.startswith("commit ") and len(raw_line) >= 47:
                current_commit = raw_line.split()[1][:8]
                continue
            if raw_line.startswith("+++ b/"):
                current_file = raw_line[6:].strip()
                continue
            if raw_line.startswith("@@ "):
                m_hunk = re.search(r"\+(\d+)", raw_line)
                if m_hunk:
                    current_line = int(m_hunk.group(1))
                continue
            if not raw_line.startswith("+") or raw_line.startswith("+++"):
                continue

            if not current_file or cls._is_ignored_path(current_file, include_tests):
                current_line += 1
                continue

            added_line = raw_line[1:]
            line_no = current_line
            current_line += 1

            if "nosec" in added_line.lower() or "vibetrace:ignore" in added_line.lower():
                continue
            if (
                "re.compile" in added_line
                or 'r"' in added_line
                or "r'" in added_line
                or "non_compliant_example=" in added_line
            ):
                continue

            # 1. Known high-confidence token formats (25+ Gitleaks rules)
            m_tok = _KNOWN_TOKEN_RE.search(added_line)
            if m_tok:
                secret_val = m_tok.group(1)
                masked = _mask_secret(secret_val)
                code_hash = hashlib.sha256(
                    f"{current_file}:{masked}:{current_commit}".encode()
                ).hexdigest()
                findings.append(
                    SecurityFinding(
                        id=f"SEC-CWE-798-{code_hash[:10]}",
                        rule_id="git:leaked-api-token",
                        cwe_id="CWE-798",
                        category=FindingCategory.GIT_SECRET,
                        severity=Severity.CRITICAL,
                        title=f"Leaked Secret Token in Git ({current_commit})",
                        message=(
                            f"Detected credential token ({masked}) in '{current_file}' "
                            f"(source: {current_commit})."
                        ),
                        file_path=current_file,
                        start_line=line_no,
                        end_line=line_no,
                        symbol_name=None,
                        code_hash=code_hash,
                        non_compliant_snippet=masked,
                        compliant_snippet='os.environ["SECRET_TOKEN"]',
                        remediation_guidance=(
                            "Revoke the leaked token immediately and load secrets from "
                            "environment variables or a secret manager."
                        ),
                        metadata={"commit": current_commit},
                    )
                )
                continue

            # 2. Database / Cache Connection URI with embedded password
            m_db = _DB_URI_SECRET_RE.search(added_line)
            if m_db:
                full_uri, db_pass = m_db.group(1), m_db.group(2)
                if db_pass.lower() not in _SAFE_PLACEHOLDERS and not db_pass.startswith(
                    ("$", "{", "%")
                ):
                    masked = full_uri.replace(db_pass, "***")
                    code_hash = hashlib.sha256(
                        f"{current_file}:{masked}:{current_commit}".encode()
                    ).hexdigest()
                    findings.append(
                        SecurityFinding(
                            id=f"SEC-CWE-798-{code_hash[:10]}",
                            rule_id="git:database-uri-password",
                            cwe_id="CWE-798",
                            category=FindingCategory.GIT_SECRET,
                            severity=Severity.CRITICAL,
                            title=f"Hardcoded Database/Broker URI Password ({current_commit})",
                            message=(
                                f"Connection URI with embedded password ({masked}) found "
                                f"in '{current_file}' (source: {current_commit})."
                            ),
                            file_path=current_file,
                            start_line=line_no,
                            end_line=line_no,
                            symbol_name=None,
                            code_hash=code_hash,
                            non_compliant_snippet=masked,
                            compliant_snippet='os.environ["DATABASE_URL"]',
                            remediation_guidance=(
                                "Store connection URIs with credentials in environment "
                                "variables (e.g. DATABASE_URL) and rotate the database password."
                            ),
                            metadata={"commit": current_commit},
                        )
                    )
                    continue

            # 3. High Shannon entropy credential assignment (>= 4.3 bits/char)
            m_ent = _HIGH_ENTROPY_ASSIGN_RE.search(added_line)
            if m_ent:
                var_name, candidate = m_ent.group(1), m_ent.group(2)
                if candidate.lower() in _SAFE_PLACEHOLDERS:
                    continue
                has_lower = any(c.islower() for c in candidate)
                has_upper = any(c.isupper() for c in candidate)
                has_digit = any(c.isdigit() for c in candidate)
                entropy = shannon_entropy(candidate)
                if entropy >= 4.3 and sum((has_lower, has_upper, has_digit)) >= 2:
                    masked = _mask_secret(candidate)
                    code_hash = hashlib.sha256(
                        f"{current_file}:{var_name}:{masked}:{current_commit}".encode()
                    ).hexdigest()
                    findings.append(
                        SecurityFinding(
                            id=f"SEC-CWE-798-{code_hash[:10]}",
                            rule_id="git:high-entropy-secret",
                            cwe_id="CWE-798",
                            category=FindingCategory.GIT_SECRET,
                            severity=Severity.HIGH,
                            title=f"High-Entropy Secret in '{var_name}' ({current_commit})",
                            message=(
                                f"High-entropy secret ({entropy:.2f} bits/char, {masked}) assigned "
                                f"to '{var_name}' in '{current_file}' (source: {current_commit})."
                            ),
                            file_path=current_file,
                            start_line=line_no,
                            end_line=line_no,
                            symbol_name=None,
                            code_hash=code_hash,
                            non_compliant_snippet=f"{var_name} = '{masked}'",
                            compliant_snippet=f"{var_name} = os.environ.get('{var_name.upper()}')",
                            remediation_guidance=(
                                "Move high-entropy credentials out of source control into "
                                "environment variables and rotate the exposed secret."
                            ),
                            metadata={"commit": current_commit, "entropy": round(entropy, 2)},
                        )
                    )

        return findings
