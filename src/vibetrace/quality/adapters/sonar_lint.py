"""SonarLint Headless Language Server Adapter (Tier-2 Optional Sidecar).

Spawns `sonarlint-ls.jar` as an isolated JSON-RPC 2.0 stdio subprocess (LGPL v3
process-boundary compliant) with an isolated `-Duser.home` H2 workspace so it
never conflicts with an active VS Code or Antigravity IDE instance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibetrace.quality.adapters.base import LinterSubEngine
from vibetrace.quality.models import QualityCategory, QualityFinding, QualitySeverity
from vibetrace.quality.rules import get_rule_spec
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

logger = logging.getLogger(__name__)

_SONAR_TOOL_NAME = "sonar"
_EXTENSIONS_DIR_NAME = "extensions"
_DOCKERFILE_NAME = "dockerfile"
_RSPEC_PREFIX = "RSPEC-"
_ERR_REPLACE = "replace"
_RPC_METHOD_KEY = "method"
_REDOS_RULE_CODE = "S8786"
_COMPLEXITY_RULE_CODE = "S3776"

SUPPORTED_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py",
        ".php",
        ".phtml",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".go",
        ".java",
        ".css",
        ".html",
        ".htm",
        ".xml",
        ".yaml",
        ".yml",
        ".sh",
        ".bash",
    }
)

_EXT_TO_LANG_ID: dict[str, str] = {
    ".py": "python",
    ".php": "php",
    ".phtml": "php",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
    ".go": "go",
    ".java": "java",
    ".css": "css",
    ".html": "html",
    ".htm": "html",
    ".xml": "xml",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".sh": "shellscript",
    ".bash": "shellscript",
}

_PREFERRED_ANALYZER_NAMES: tuple[str, ...] = (
    "sonarpython.jar",
    "sonarphp.jar",
    "sonarjs.jar",
    "sonarjavascript.jar",
    "sonarjava.jar",
    "sonarjavasymbolicexecution.jar",
    "sonargo.jar",
    "sonartext.jar",
    "sonariac.jar",
    "sonarhtml.jar",
    "sonarxml.jar",
)

_SECURITY_RULE_CODES: frozenset[str] = frozenset(
    {
        "S2068",  # Hardcoded credentials
        "S2076",  # OS command injection
        "S2077",  # SQL queries formatting
        "S2083",  # Path traversal
        "S2631",  # Regular expression DoS
        "S3649",  # SQL injection
        "S4423",  # Weak SSL/TLS protocols
        "S4426",  # Weak cryptographic keys
        "S4507",  # Debug mode enabled
        "S4790",  # Weak hashing algorithms
        "S4830",  # Server certificates not verified
        "S5131",  # XSS
        "S5144",  # SSRF
        "S5146",  # Open redirect
        "S5332",  # Clear-text protocols (http/ftp)
        "S5542",  # Encryption algorithms with weak mode
        "S5547",  # Weak cipher algorithms
        "S5659",  # JWT not signed / weak signature
        "S5883",  # OS argument injection
        "S6287",  # Hardcoded secret/token
        "S6350",  # Command argument injection
        _REDOS_RULE_CODE,  # Super-linear regex backtracking (ReDoS / CWE-1333)
    }
)

_COMPLEXITY_SHORT_CODES: frozenset[str] = frozenset(
    {_COMPLEXITY_RULE_CODE, "S1541", "S134", "S1066", "S5843"}
)
_MAINTAINABILITY_SHORT_CODES: frozenset[str] = frozenset(
    {"S1144", "S107", "S1481", "S1854", "S1192"}
)


@dataclass(frozen=True)
class SonarRuntimePaths:
    """Resolved file system paths for executing the SonarLint headless engine."""

    root_dir: Path
    java_bin: Path
    ls_jar: Path
    analyzer_jars: tuple[Path, ...]
    work_dir: Path
    source_kind: str  # "vibetrace" | "antigravity-ide" | "vscode" | "cursor"


def _find_java_binary(sonar_root: Path) -> Path | None:
    """Locates the embedded Java 21+ binary inside `jre/` or falls back to system `java`."""
    java_name = "java.exe" if os.name == "nt" else "java"
    jre_dir = sonar_root / "jre"
    if jre_dir.is_dir():
        for candidate in jre_dir.rglob(java_name):
            if candidate.is_file() and candidate.parent.name == "bin":
                return candidate
    sys_java = shutil.which(java_name)
    return Path(sys_java) if sys_java else None


def _collect_fallback_analyzers(analyzers_dir: Path, seen: set[str]) -> list[Path]:
    """Collects any remaining `sonar*.jar` files excluding omnisharp."""
    extras: list[Path] = []
    for jar in sorted(analyzers_dir.glob("sonar*.jar")):
        if jar.is_file() and jar.name not in seen and "omnisharp" not in jar.name.lower():
            extras.append(jar)
    return extras


def _collect_analyzer_jars(sonar_root: Path) -> tuple[Path, ...]:
    """Collects valid analyzer JARs from `analyzers/` inside the Sonar bundle."""
    analyzers_dir = sonar_root / "analyzers"
    if not analyzers_dir.is_dir():
        return ()
    selected: list[Path] = []
    seen: set[str] = set()
    for name in _PREFERRED_ANALYZER_NAMES:
        jar = analyzers_dir / name
        if jar.is_file():
            selected.append(jar)
            seen.add(jar.name)
    if not selected:
        selected.extend(_collect_fallback_analyzers(analyzers_dir, seen))
    return tuple(selected)


def _discover_candidate_roots(home: Path) -> list[tuple[str, Path]]:
    """Builds the ordered list of candidate `(source_kind, root_dir)` directories."""
    candidates: list[tuple[str, Path]] = []
    vt_sonar = home / ".vibetrace" / "tools" / _SONAR_TOOL_NAME
    if vt_sonar.is_dir():
        candidates.append(("vibetrace", vt_sonar))
        ext_sub = vt_sonar / "extension"
        if ext_sub.is_dir():
            candidates.append(("vibetrace", ext_sub))

    ide_roots = (
        ("antigravity-ide", home / ".antigravity-ide" / _EXTENSIONS_DIR_NAME),
        ("vscode", home / ".vscode" / _EXTENSIONS_DIR_NAME),
        ("cursor", home / ".cursor" / _EXTENSIONS_DIR_NAME),
        ("vscode-insiders", home / ".vscode-insiders" / _EXTENSIONS_DIR_NAME),
    )
    for label, ext_root in ide_roots:
        if not ext_root.is_dir():
            continue
        matches = sorted(
            ext_root.glob("sonarsource.sonarlint-vscode*"),
            key=lambda p: p.name,
            reverse=True,
        )
        candidates.extend((label, m) for m in matches if m.is_dir())
    return candidates


def resolve_sonar_runtime(
    custom_home: Path | None = None,
) -> SonarRuntimePaths | None:
    """Discovers the SonarLint Language Server runtime across VibeTrace or local IDE extensions."""
    home = custom_home or Path.home()
    work_dir = home / ".vibetrace" / "tools" / _SONAR_TOOL_NAME / "work"

    for source_kind, root_dir in _discover_candidate_roots(home):
        ls_jar = root_dir / "server" / "sonarlint-ls.jar"
        if not ls_jar.is_file():
            continue
        analyzers = _collect_analyzer_jars(root_dir)
        java_bin = _find_java_binary(root_dir)
        if analyzers and java_bin is not None:
            return SonarRuntimePaths(
                root_dir=root_dir,
                java_bin=java_bin,
                ls_jar=ls_jar,
                analyzer_jars=analyzers,
                work_dir=work_dir,
                source_kind=source_kind,
            )

    return None


def _path_to_file_uri(path: Path) -> str:
    """Converts a local Path to a standard RFC 8089 `file://` URI accepted by SonarLint LSP."""
    return path.resolve().as_uri()


def _file_uri_to_rel_path(uri: str, project_root: Path) -> str:
    """Converts a `file://` URI back to a project-relative POSIX path."""
    from urllib.parse import unquote, urlparse

    parsed = urlparse(uri)
    raw_path = unquote(parsed.path or "")
    if os.name == "nt" and raw_path.startswith("/") and len(raw_path) > 2 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    try:
        abs_p = Path(raw_path).resolve()
        return abs_p.relative_to(project_root.resolve()).as_posix()
    except Exception:
        return raw_path.replace("\\", "/")


def _language_id_for_file(file_path: Path) -> str:
    """Maps a source file to its SonarLint LSP `languageId`."""
    lower_name = file_path.name.lower()
    if lower_name == _DOCKERFILE_NAME or lower_name.startswith("dockerfile."):
        return _DOCKERFILE_NAME
    return _EXT_TO_LANG_ID.get(file_path.suffix.lower(), "plaintext")


def extract_rspec_short(rule_id: str) -> str:
    """Extracts the canonical short code (e.g. 'S3776') from 'python:S3776' or 'RSPEC-3776'."""
    tail = rule_id.split(":")[-1].strip()
    if tail.upper().startswith(_RSPEC_PREFIX):
        return f"S{tail[6:]}"
    return tail


def sys_platform_name() -> str:
    """Returns VS Code platform identifier ('win32', 'darwin', 'linux')."""
    if os.name == "nt":
        return "win32"
    if platform.system().lower() == "darwin":
        return "darwin"
    return "linux"


def _read_lsp_headers(stream: Any) -> int | None:
    """Reads LSP headers from stdout stream and returns Content-Length (or None on EOF)."""
    content_length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        line_str = line.decode("ascii", errors=_ERR_REPLACE).strip()
        if not line_str:
            return content_length
        if line_str.lower().startswith("content-length:"):
            try:
                content_length = int(line_str.split(":", 1)[1].strip())
            except ValueError:
                content_length = 0


def _read_exact_bytes(stream: Any, num_bytes: int) -> bytes | None:
    """Reads exact number of bytes from stream or returns None on premature EOF."""
    body = b""
    while len(body) < num_bytes:
        chunk = stream.read(num_bytes - len(body))
        if not chunk:
            return None
        body += chunk
    return body


class _SonarJsonRpcSession:
    """Manages a single batch JSON-RPC 2.0 stdio session with `sonarlint-ls.jar`."""

    def __init__(
        self,
        runtime: SonarRuntimePaths,
        project_root: Path,
        target_files: list[Path],
    ) -> None:
        self.runtime = runtime
        self.project_root = project_root.resolve()
        self.target_files = [f.resolve() for f in target_files]
        self._proc: subprocess.Popen[bytes] | None = None
        self._write_lock = threading.Lock()
        self._diagnostics_by_uri: dict[str, list[dict[str, Any]]] = {}
        self._published_uris: set[str] = set()
        self._init_done = threading.Event()
        self._ready_to_analyze = threading.Event()
        self._closed = False

    def _send_message(self, payload: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None or self._closed:
            return
        body = json.dumps(payload).encode("utf-8")
        header = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii")
        with self._write_lock:
            try:
                self._proc.stdin.write(header + body)
                self._proc.stdin.flush()
            except OSError:
                pass

    def _build_found_files_dto(self) -> list[dict[str, Any]]:
        dtos: list[dict[str, Any]] = []
        for f_path in self.target_files:
            if not f_path.is_file():
                continue
            try:
                content = f_path.read_text(encoding="utf-8", errors=_ERR_REPLACE)
            except OSError:
                content = ""
            dtos.append(
                {
                    "fileName": f_path.name,
                    "filePath": str(f_path),
                    "content": content,
                }
            )
        return dtos

    def _handle_server_request(self, msg: dict[str, Any]) -> None:
        req_id = msg.get("id")
        method = msg.get(_RPC_METHOD_KEY, "")
        params = msg.get("params") or {}

        if method == "sonarlint/listFilesInFolder":
            result: Any = {"foundFiles": self._build_found_files_dto()}
        elif method == "workspace/configuration":
            items = params.get("items") or [1]
            result = [
                {
                    "disableTelemetry": True,
                    "testFilePattern": "",
                    "analyzerProperties": {},
                }
                for _ in items
            ]
        elif method in ("sonarlint/isOpenInEditor", "sonarlint/shouldAnalyseFile"):
            result = True
        elif method == "sonarlint/filterOutExcludedFiles":
            file_uris = params.get("fileUris") or [_path_to_file_uri(f) for f in self.target_files]
            result = {"fileUris": file_uris}
        else:
            result = None

        self._send_message({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _handle_notification(self, msg: dict[str, Any]) -> None:
        method = msg.get(_RPC_METHOD_KEY, "")
        params = msg.get("params") or {}

        if method == "textDocument/publishDiagnostics":
            uri = params.get("uri", "")
            diags = params.get("diagnostics") or []
            if uri:
                self._diagnostics_by_uri[uri] = diags
                self._published_uris.add(uri)
        elif method == "window/logMessage":
            log_msg = str(params.get("message") or "")
            if any(
                marker in log_msg
                for marker in (
                    "Started security hotspot",
                    "Standalone mode",
                    "Binding suggestion",
                    "Folder ",
                )
            ):
                self._ready_to_analyze.set()

    def _dispatch_message(self, msg: dict[str, Any]) -> None:
        if "id" in msg and _RPC_METHOD_KEY in msg:
            self._handle_server_request(msg)
        elif "id" in msg and "result" in msg and msg.get("id") == 1:
            self._init_done.set()
        elif _RPC_METHOD_KEY in msg:
            self._handle_notification(msg)

    def _reader_loop(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        stream = self._proc.stdout
        try:
            while not self._closed:
                content_length = _read_lsp_headers(stream)
                if content_length is None:
                    return
                if content_length <= 0:
                    continue
                body = _read_exact_bytes(stream, content_length)
                if body is None:
                    return
                try:
                    msg = json.loads(body.decode("utf-8", errors=_ERR_REPLACE))
                except json.JSONDecodeError:
                    continue
                self._dispatch_message(msg)
        except Exception as exc:
            logger.debug("SonarLint JSON-RPC reader exited: %s", exc)

    def _open_target_documents(self) -> set[str]:
        expected_uris: set[str] = set()
        for f_path in self.target_files:
            if not f_path.is_file():
                continue
            try:
                text = f_path.read_text(encoding="utf-8", errors=_ERR_REPLACE)
            except OSError:
                continue
            uri = _path_to_file_uri(f_path)
            expected_uris.add(uri)
            self._send_message(
                {
                    "jsonrpc": "2.0",
                    _RPC_METHOD_KEY: "textDocument/didOpen",
                    "params": {
                        "textDocument": {
                            "uri": uri,
                            "languageId": _language_id_for_file(f_path),
                            "version": 1,
                            "text": text,
                        }
                    },
                }
            )
        return expected_uris

    def _await_diagnostics(self, expected_uris: set[str], timeout_sec: float) -> None:
        deadline = time.monotonic() + timeout_sec
        last_count = 0
        stable_ticks = 0
        while time.monotonic() < deadline:
            time.sleep(0.25)
            if self._published_uris < expected_uris:
                continue
            total_diags = sum(len(v) for v in self._diagnostics_by_uri.values())
            target_ticks = 4 if total_diags > 0 else 8
            if total_diags != last_count:
                last_count = total_diags
                stable_ticks = 0
                continue
            stable_ticks += 1
            if stable_ticks >= target_ticks:
                return

    def _shutdown_process(self) -> None:
        self._closed = True
        if self._proc is None:
            return
        try:
            self._send_message({"jsonrpc": "2.0", "id": 999, _RPC_METHOD_KEY: "shutdown"})
            self._send_message({"jsonrpc": "2.0", _RPC_METHOD_KEY: "exit"})
            self._proc.terminate()
            self._proc.wait(timeout=2.0)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass

    def run(self, timeout_sec: float = 40.0) -> dict[str, list[dict[str, Any]]]:
        """Executes the LSP session, analyzes target files, and returns raw diagnostics by URI."""
        work_dir = self.runtime.work_dir
        telemetry_dir = work_dir / "telemetry"
        work_dir.mkdir(parents=True, exist_ok=True)
        telemetry_dir.mkdir(parents=True, exist_ok=True)

        cmd: list[str] = [
            str(self.runtime.java_bin),
            f"-Duser.home={work_dir}",
            "-jar",
            str(self.runtime.ls_jar),
            "-stdio",
            "-analyzers",
            *[str(j) for j in self.runtime.analyzer_jars],
        ]

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=str(self.project_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            logger.warning("Failed to spawn SonarLint Language Server: %s", exc)
            return {}

        threading.Thread(target=self._reader_loop, daemon=True).start()

        try:
            root_uri = _path_to_file_uri(self.project_root)
            arch = "arm64" if "arm" in platform.machine().lower() else "x64"
            init_req = {
                "jsonrpc": "2.0",
                "id": 1,
                _RPC_METHOD_KEY: "initialize",
                "params": {
                    "processId": os.getpid(),
                    "rootUri": root_uri,
                    "capabilities": {
                        "workspace": {"configuration": True, "workspaceFolders": True},
                        "textDocument": {"publishDiagnostics": {"relatedInformation": True}},
                    },
                    "workspaceFolders": [{"uri": root_uri, "name": self.project_root.name}],
                    "initializationOptions": {
                        "showVerboseLogs": True,
                        "productKey": "vscode",
                        "productName": "SonarLint",
                        "productVersion": "6.2.0",
                        "telemetryStorage": str(telemetry_dir),
                        "platform": sys_platform_name(),
                        "architecture": arch,
                        "additionalAttributes": {},
                    },
                },
            }
            self._send_message(init_req)
            if not self._init_done.wait(timeout=min(15.0, timeout_sec)):
                return {}

            self._send_message({"jsonrpc": "2.0", _RPC_METHOD_KEY: "initialized", "params": {}})
            self._ready_to_analyze.wait(timeout=3.5)

            expected_uris = self._open_target_documents()
            self._await_diagnostics(expected_uris, timeout_sec)
            return dict(self._diagnostics_by_uri)
        finally:
            self._shutdown_process()


def _resolve_finding_severity(lsp_sev: int, short_code: str) -> QualitySeverity:
    if lsp_sev == 1 or short_code in (_COMPLEXITY_RULE_CODE, _REDOS_RULE_CODE):
        return QualitySeverity.HIGH
    if lsp_sev == 2:
        return QualitySeverity.MEDIUM
    return QualitySeverity.LOW


def _resolve_finding_category(short_code: str) -> QualityCategory:
    if short_code in _COMPLEXITY_SHORT_CODES:
        return QualityCategory.COMPLEXITY
    if short_code in _MAINTAINABILITY_SHORT_CODES:
        return QualityCategory.MAINTAINABILITY
    return QualityCategory.CODE_SMELL


def _extract_source_line_snippet(abs_file: Path, line_number: int) -> str:
    if not abs_file.is_file():
        return ""
    try:
        lines = abs_file.read_text(encoding="utf-8", errors=_ERR_REPLACE).splitlines()
        if 1 <= line_number <= len(lines):
            return lines[line_number - 1].strip()
    except OSError:
        pass
    return ""


def _extract_diag_line_range(diag: dict[str, Any]) -> tuple[int, int]:
    rng = diag.get("range") or {}
    start_pos = rng.get("start") or {}
    end_pos = rng.get("end") or {}
    start_line = int(start_pos.get("line", 0)) + 1
    end_line = max(start_line, int(end_pos.get("line", start_line - 1)) + 1)
    return start_line, end_line


def _normalize_diag_rule_ids(diag: dict[str, Any]) -> tuple[str, str, str]:
    raw_code = str(diag.get("code") or "sonar:S0000")
    rule_id = raw_code if ":" in raw_code else f"sonar:{raw_code}"
    short_code = extract_rspec_short(rule_id)
    suffix_num = short_code[1:] if short_code.upper().startswith("S") else short_code
    return rule_id, short_code, f"{_RSPEC_PREFIX}{suffix_num}"


def _map_quality_to_security_severity(qual_sev: QualitySeverity) -> Severity:
    if qual_sev == QualitySeverity.HIGH:
        return Severity.HIGH
    if qual_sev == QualitySeverity.MEDIUM:
        return Severity.MEDIUM
    return Severity.LOW


class SonarLintAdapter(LinterSubEngine):
    """Optional Tier-2 Headless SonarLint Engine Adapter."""

    name = _SONAR_TOOL_NAME
    SUPPORTED_EXTENSIONS = SUPPORTED_EXTENSIONS

    def __init__(self, timeout_sec: float = 40.0) -> None:
        self.timeout_sec = timeout_sec

    def is_available(self, project_root: Path | None = None) -> bool:
        """Returns True if `sonarlint-ls.jar`, analyzers, and a JRE are available locally."""
        return resolve_sonar_runtime() is not None

    @staticmethod
    def _filter_supported_files(
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[Path]:
        if file_list is not None:
            candidates = file_list
        else:
            from vibetrace.quality.collector import collect_source_files

            candidates = collect_source_files(project_root=project_root, include_tests=False)

        supported: list[Path] = []
        for f in candidates:
            p = f if f.is_absolute() else (project_root / f)
            lower_name = p.name.lower()
            is_docker = lower_name == _DOCKERFILE_NAME or lower_name.startswith("dockerfile.")
            if (p.suffix.lower() in SUPPORTED_EXTENSIONS or is_docker) and p.is_file():
                supported.append(p.resolve())
        return supported

    @staticmethod
    def _map_diagnostic_to_quality_finding(
        diag: dict[str, Any],
        rel_path: str,
        project_root: Path,
    ) -> QualityFinding:
        rule_id, short_code, rspec_id = _normalize_diag_rule_ids(diag)
        start_line, end_line = _extract_diag_line_range(diag)
        severity = _resolve_finding_severity(int(diag.get("severity") or 2), short_code)
        category = _resolve_finding_category(short_code)

        message = str(diag.get("message") or "SonarLint issue detected").strip()
        spec = get_rule_spec(rule_id)
        title = spec.title if spec else message.split("\n")[0][:120]
        snippet = _extract_source_line_snippet(project_root / rel_path, start_line)

        hash_basis = f"{rel_path}:{rule_id}:{start_line}:{snippet}"
        code_hash = hashlib.sha256(hash_basis.encode("utf-8")).hexdigest()
        cwe_list = list(spec.cwe_ids) if (spec and spec.cwe_ids) else []

        return QualityFinding(
            id=f"QUAL-{short_code}-{code_hash[:12]}",
            rule_id=rule_id,
            rspec_id=rspec_id,
            cwe_id=cwe_list[0] if cwe_list else None,
            cwe_ids=cwe_list,
            owasp_category=spec.owasp if spec else None,
            category=category,
            severity=severity,
            title=title,
            message=message,
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            code_hash=code_hash,
            non_compliant_snippet=snippet or None,
            compliant_snippet=spec.compliant_example if spec else None,
            remediation_guidance=(
                spec.explanation if spec else "Resolve SonarLint rule violation."
            ),
            metadata={"engine": "sonarlint-ls"},
        )

    def audit_file(self, file_path: Path, project_root: Path) -> list[QualityFinding]:
        """Audits a single file using the headless SonarLint Language Server."""
        return self.audit_project(project_root=project_root, file_list=[file_path])

    def audit_project(
        self,
        project_root: Path,
        file_list: list[Path] | None = None,
    ) -> list[QualityFinding]:
        """Audits target files via a single batch JSON-RPC session with `sonarlint-ls.jar`."""
        runtime = resolve_sonar_runtime()
        if runtime is None:
            return []

        targets = self._filter_supported_files(project_root, file_list)
        if not targets:
            return []

        session = _SonarJsonRpcSession(
            runtime=runtime,
            project_root=project_root,
            target_files=targets,
        )
        raw_by_uri = session.run(timeout_sec=self.timeout_sec)

        findings: list[QualityFinding] = []
        for uri, diags in raw_by_uri.items():
            rel_path = _file_uri_to_rel_path(uri, project_root)
            for d in diags:
                findings.append(self._map_diagnostic_to_quality_finding(d, rel_path, project_root))
        return findings

    def scan_security_findings(
        self,
        project_root: Path,
        target_files: list[str] | None = None,
    ) -> list[SecurityFinding]:
        """Runs SonarLint and extracts security/vulnerability/ReDoS rules as `SecurityFinding`."""
        path_list = [project_root / f for f in target_files] if target_files else None
        qual_findings = self.audit_project(project_root=project_root, file_list=path_list)
        sec_findings: list[SecurityFinding] = []

        for qf in qual_findings:
            short_code = extract_rspec_short(qf.rule_id)
            if short_code not in _SECURITY_RULE_CODES and not qf.cwe_id:
                continue
            default_cwe = "CWE-1333" if short_code == _REDOS_RULE_CODE else "CWE-710"
            cwe = qf.cwe_id or default_cwe
            sec_findings.append(
                SecurityFinding(
                    id=f"SEC-SONAR-{short_code}-{qf.code_hash[:10]}",
                    rule_id=qf.rule_id,
                    cwe_id=cwe,
                    cwe_ids=qf.cwe_ids or [cwe],
                    owasp_category=qf.owasp_category or "A05:2021-Security Misconfiguration",
                    category=FindingCategory.AST_PATTERN,
                    severity=_map_quality_to_security_severity(qf.severity),
                    title=qf.title,
                    message=qf.message,
                    file_path=qf.file_path,
                    start_line=qf.start_line,
                    end_line=qf.end_line,
                    symbol_name=qf.symbol_name,
                    code_hash=qf.code_hash,
                    non_compliant_snippet=qf.non_compliant_snippet,
                    remediation_guidance=qf.remediation_guidance,
                    metadata={"scanner": "sonarlint-ls", "rspec_id": qf.rspec_id},
                )
            )
        return sec_findings
