"""Zero-dependency Infrastructure-as-Code (IaC) Security Scanner (Docker, Compose, K8s, GHA)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

_FROM_LATEST_RE = re.compile(
    r"^\s*FROM\s+(?:--platform=\S+\s+)?([^\s:#]+)(?::latest)?(?:\s|$)", re.I
)
_CURL_PIPE_SH_RE = re.compile(r"\b(?:curl|wget)\b[^|\n]*\|\s*(?:ba|z|d)?sh\b", re.I)
_ADD_REMOTE_RE = re.compile(r"^\s*ADD\s+https?://", re.I)
_DOCKER_ENV_SECRET_RE = re.compile(
    r"^\s*(?:ENV|ARG)\s+[A-Z0-9_]*(?:SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|TOKEN)"
    r"[A-Z0-9_]*\s*=\s*['\"]?([^\s'\"]{6,})",
    re.I,
)
_USER_DIRECTIVE_RE = re.compile(r"^\s*USER\s+(\S+)", re.I)

_COMPOSE_PRIVILEGED_RE = re.compile(r"^\s*privileged\s*:\s*true\b", re.I)
_DOCKER_SOCK_RE = re.compile(r"/var/run/docker\.sock\b")
_HOST_NET_RE = re.compile(
    r"^\s*(?:network_mode\s*:\s*['\"]?host['\"]?|hostNetwork\s*:\s*true)\b", re.I
)
_K8S_ALLOW_PRIV_ESC_RE = re.compile(r"^\s*allowPrivilegeEscalation\s*:\s*true\b", re.I)

_GHA_INJECTION_RE = re.compile(
    r"\$\{\{\s*github\.event\.(?:issue\.title|issue\.body|pull_request\.title|"
    r"pull_request\.body|comment\.body|review\.body|commits\[[^\]]*\]\.message|"
    r"head_commit\.message|pages\.[^\}]*|discussion\.title|discussion\.body)\s*\}\}"
    r"|\$\{\{\s*github\.head_ref\s*\}\}",
    re.I,
)


def _make_finding(
    rule_id: str,
    cwe_id: str,
    severity: Severity,
    title: str,
    message: str,
    file_path: str,
    line_no: int,
    snippet: str,
    compliant: str,
    guidance: str,
) -> SecurityFinding:
    norm_path = file_path.replace("\\", "/")
    code_hash = hashlib.sha256(f"{norm_path}:{rule_id}:{snippet.strip()}".encode()).hexdigest()
    fid = f"SEC-{cwe_id}-{code_hash[:10]}"
    return SecurityFinding(
        id=fid,
        rule_id=rule_id,
        cwe_id=cwe_id,
        category=FindingCategory.IAC_MISCONFIG,
        severity=severity,
        title=title,
        message=message,
        file_path=norm_path,
        start_line=line_no,
        end_line=line_no,
        symbol_name=None,
        code_hash=code_hash,
        non_compliant_snippet=snippet.strip(),
        compliant_snippet=compliant,
        remediation_guidance=guidance,
    )


class IacScanner:
    """Scans Dockerfile, docker-compose, Kubernetes manifests, and GitHub Actions workflows."""

    @classmethod
    def scan_project(
        cls, project_root: Path, file_filter: str | None = None
    ) -> list[SecurityFinding]:
        """Discovers and scans IaC configuration files in the repository."""
        findings: list[SecurityFinding] = []
        candidates = cls._discover_iac_files(project_root, file_filter)
        for rel_path in candidates:
            abs_path = project_root / rel_path
            try:
                content = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            fname = abs_path.name.lower()
            norm_rel = rel_path.replace("\\", "/")
            if (
                fname == "dockerfile"
                or fname.startswith("dockerfile.")
                or fname.endswith(".dockerfile")
            ):
                findings.extend(cls._scan_dockerfile(norm_rel, content))
            elif ".github/workflows/" in norm_rel and fname.endswith((".yml", ".yaml")):
                findings.extend(cls._scan_github_workflow(norm_rel, content))
            elif fname.endswith((".yml", ".yaml")):
                findings.extend(cls._scan_compose_or_k8s(norm_rel, content))
        return findings

    @staticmethod
    def _discover_iac_files(project_root: Path, file_filter: str | None = None) -> list[str]:
        if file_filter:
            return [file_filter.replace("\\", "/")]
        found: list[str] = []
        skip_dirs = {".git", "node_modules", ".venv", "venv", "target", "dist", "build", ".next"}
        for path in project_root.rglob("*"):
            if not path.is_file():
                continue
            rel_parts = path.relative_to(project_root).parts
            if any(p in skip_dirs for p in rel_parts[:-1]):
                continue
            fname = path.name.lower()
            rel_str = str(path.relative_to(project_root)).replace("\\", "/")
            if (
                fname == "dockerfile"
                or fname.startswith(("dockerfile.", "docker-compose"))
                or fname.endswith(".dockerfile")
                or fname in ("compose.yml", "compose.yaml")
                or ".github/workflows/" in rel_str
                or any(k in rel_str.lower() for k in ("k8s/", "kubernetes/", "helm/", "manifests/"))
            ):
                found.append(rel_str)
        return sorted(set(found))

    @classmethod
    def _scan_dockerfile(cls, file_path: str, content: str) -> list[SecurityFinding]:
        lines = content.splitlines()
        findings: list[SecurityFinding] = []
        has_non_root_user = False
        last_from_line = 0
        last_from_text = ""

        for idx, raw in enumerate(lines, 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if "nosec" in stripped.lower() or "vibetrace:ignore" in stripped.lower():
                continue

            m_from = _FROM_LATEST_RE.match(stripped)
            if m_from:
                base_img = m_from.group(1)
                last_from_line = idx
                last_from_text = stripped
                if base_img.lower() != "scratch" and (
                    ":latest" in stripped.lower() or ":" not in stripped.split()[1]
                ):
                    findings.append(
                        _make_finding(
                            rule_id="iac:dockerfile-unpinned-image",
                            cwe_id="CWE-829",
                            severity=Severity.MEDIUM,
                            title="Unpinned or ':latest' Container Base Image",
                            message=(
                                "Using ':latest' or an untagged image exposes builds "
                                "to mutable supply-chain changes."
                            ),
                            file_path=file_path,
                            line_no=idx,
                            snippet=stripped,
                            compliant="FROM python:3.12.7-slim@sha256:...",
                            guidance=(
                                "Pin Docker base images to an explicit version tag "
                                "or SHA-256 digest."
                            ),
                        )
                    )

            m_user = _USER_DIRECTIVE_RE.match(stripped)
            if m_user and m_user.group(1).lower() not in ("root", "0"):
                has_non_root_user = True

            if _CURL_PIPE_SH_RE.search(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:dockerfile-curl-pipe-sh",
                        cwe_id="CWE-494",
                        severity=Severity.HIGH,
                        title="Remote Script Piped Directly to Shell in Dockerfile",
                        message=(
                            "Piping curl/wget output directly into sh/bash executes "
                            "unverified remote code."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant=(
                            "RUN curl -fsSL https://... -o install.sh "
                            "&& sha256sum -c ... && sh install.sh"
                        ),
                        guidance=(
                            "Download artifacts to a file, verify their SHA-256 checksum, "
                            "and then execute."
                        ),
                    )
                )

            if _ADD_REMOTE_RE.match(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:dockerfile-add-remote-url",
                        cwe_id="CWE-829",
                        severity=Severity.MEDIUM,
                        title="Dockerfile ADD with Remote URL",
                        message=(
                            "ADD with a remote URL fetches content without checksum verification."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant="COPY local-file /app/",
                        guidance=(
                            "Use COPY for local files or curl with sha256sum verification "
                            "for remote downloads."
                        ),
                    )
                )

            if _DOCKER_ENV_SECRET_RE.match(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:dockerfile-env-secret",
                        cwe_id="CWE-798",
                        severity=Severity.CRITICAL,
                        title="Hardcoded Secret in Dockerfile ENV/ARG",
                        message=(
                            "Secrets baked into ENV or ARG persist permanently in "
                            "Docker image layer metadata."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant="RUN --mount=type=secret,id=my_secret ...",
                        guidance=(
                            "Pass secrets at runtime or use BuildKit '--mount=type=secret' "
                            "instead of ENV/ARG."
                        ),
                    )
                )

        if last_from_line > 0 and not has_non_root_user:
            findings.append(
                _make_finding(
                    rule_id="iac:dockerfile-missing-user",
                    cwe_id="CWE-250",
                    severity=Severity.HIGH,
                    title="Container Runs as Root (Missing USER Directive)",
                    message=(
                        "Dockerfile does not specify a non-root USER; container processes "
                        "will execute as root (UID 0)."
                    ),
                    file_path=file_path,
                    line_no=last_from_line,
                    snippet=last_from_text,
                    compliant="RUN useradd -r -u 1001 appuser\nUSER appuser",
                    guidance="Create and switch to an unprivileged non-root USER before CMD.",
                )
            )

        return findings

    @classmethod
    def _scan_compose_or_k8s(cls, file_path: str, content: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for idx, raw in enumerate(content.splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _COMPOSE_PRIVILEGED_RE.match(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:container-privileged-mode",
                        cwe_id="CWE-250",
                        severity=Severity.CRITICAL,
                        title="Privileged Container Execution Enabled",
                        message=(
                            "Running a container with 'privileged: true' grants root "
                            "capabilities on the host kernel."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant="privileged: false\ncap_drop: [ALL]",
                        guidance="Disable privileged mode and grant only minimal capabilities.",
                    )
                )
            if _DOCKER_SOCK_RE.search(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:docker-socket-mount",
                        cwe_id="CWE-250",
                        severity=Severity.CRITICAL,
                        title="Host Docker Socket Mounted Inside Container",
                        message=(
                            "Mounting '/var/run/docker.sock' gives the container "
                            "root-equivalent control over the host daemon."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant="# Remove /var/run/docker.sock volume mount",
                        guidance=(
                            "Avoid mounting the host Docker socket or isolate it behind "
                            "a read-only socket proxy."
                        ),
                    )
                )
            if _HOST_NET_RE.match(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:container-host-network",
                        cwe_id="CWE-250",
                        severity=Severity.HIGH,
                        title="Host Network Namespace Shared with Container",
                        message="Enabling host networking bypasses container network isolation.",
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant="# Use bridge network with explicit port mappings",
                        guidance="Use isolated bridge networking and publish only required ports.",
                    )
                )
            if _K8S_ALLOW_PRIV_ESC_RE.match(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:k8s-allow-privilege-escalation",
                        cwe_id="CWE-250",
                        severity=Severity.HIGH,
                        title="Kubernetes allowPrivilegeEscalation Enabled",
                        message=(
                            "Allowing privilege escalation permits child processes to gain "
                            "more privileges than their parent."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant="allowPrivilegeEscalation: false",
                        guidance="Set securityContext.allowPrivilegeEscalation to false.",
                    )
                )
        return findings

    @classmethod
    def _scan_github_workflow(cls, file_path: str, content: str) -> list[SecurityFinding]:
        findings: list[SecurityFinding] = []
        for idx, raw in enumerate(content.splitlines(), 1):
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if _GHA_INJECTION_RE.search(stripped):
                findings.append(
                    _make_finding(
                        rule_id="iac:github-actions-untrusted-input",
                        cwe_id="CWE-94",
                        severity=Severity.CRITICAL,
                        title="GitHub Actions Untrusted Event Interpolation",
                        message=(
                            "Interpolating untrusted PR/issue fields directly in a workflow "
                            "allows arbitrary command injection."
                        ),
                        file_path=file_path,
                        line_no=idx,
                        snippet=stripped,
                        compliant=(
                            "env:\n  PR_TITLE: ${{ github.event.pull_request.title }}\n"
                            'run: echo "$PR_TITLE"'
                        ),
                        guidance=(
                            "Bind untrusted github.event values to an environment variable "
                            "and reference it quoted in shell."
                        ),
                    )
                )
        return findings
