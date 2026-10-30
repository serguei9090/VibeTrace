"""AST Security Scanner that inspects Tree-sitter parse trees for security anti-patterns."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from tree_sitter import Node

from vibetrace.indexer.parsers import ParserFactory
from vibetrace.security.models import FindingCategory, SecurityFinding
from vibetrace.security.rules import RULES_CATALOG

_SECRET_KEYWORD_RE = re.compile(
    r"(?i)(api[_-]?key|secret|password|passwd|auth[_-]?token|private[_-]?key|access[_-]?token)"
)
_DUMMY_SECRET_VALS = {
    "",
    "none",
    "null",
    "undefined",
    "true",
    "false",
    "test",
    "demo",
    "sample",
    "example",
    "placeholder",
    "dummy",
    "default",
    "changeme",
    "your_api_key",
    "xxx",
    "todo",
    "admin",
}


_JS_LANGS = ("javascript", "typescript", "tsx")
_SAFE_SQL_INTERPS = {"placeholders", "placeholder", "qmarks", "alias", "table", "table_name"}
_NOSEC_RE = re.compile(r"(?:#|//)\s*nosec\b", re.IGNORECASE)


def _is_static_str_expr(node: Node) -> bool:
    """Returns True if an AST expression is a pure static string literal or ternary of literals."""
    if node.type in (
        "string",
        "string_literal",
        "raw_string_literal",
        "interpreted_string_literal",
    ):
        return not any(c.type in ("interpolation", "template_substitution") for c in node.children)
    if node.type == "template_string":
        return not any(c.type == "template_substitution" for c in node.children)
    if node.type in ("conditional_expression", "ternary_expression"):
        conseq = node.child_by_field_name("consequence")
        alt = node.child_by_field_name("alternative")
        return bool(conseq and alt and _is_static_str_expr(conseq) and _is_static_str_expr(alt))
    return False


def _calc_code_hash(code_str: str) -> str:
    """Calculates normalized SHA-256 hash ignoring surrounding whitespace and comments."""
    normalized = "\n".join(
        line.strip()
        for line in code_str.splitlines()
        if line.strip() and not line.strip().startswith(("#", "//", "/*", "*"))
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class AstSecurityScanner:
    """Walks Tree-sitter AST nodes to detect common static security vulnerabilities."""

    @classmethod
    def scan_file(cls, file_path: str, project_root: Path) -> list[SecurityFinding]:
        """Scans a single source file for AST security defects."""
        abs_path = (project_root / file_path).resolve()
        if not abs_path.is_file():
            return []

        parser_wrapper = ParserFactory.get_parser(file_path)
        if not parser_wrapper:
            return []

        try:
            source_bytes = abs_path.read_bytes()
        except OSError:
            return []

        if not source_bytes.strip():
            return []

        try:
            tree = parser_wrapper.parse(source_bytes)
        except Exception:
            return []

        lines = source_bytes.decode("utf-8", errors="replace").splitlines()
        findings: list[SecurityFinding] = []

        cls._visit_node(
            node=tree.root_node,
            file_path=file_path,
            lines=lines,
            findings=findings,
            lang=parser_wrapper.language_name,
        )

        return [
            f
            for f in findings
            if not _NOSEC_RE.search(
                "\n".join(lines[max(0, f.start_line - 1) : min(len(lines), f.end_line)])
            )
        ]

    @classmethod
    def _visit_node(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        """Recursively checks tree nodes against security patterns."""
        cls._check_command_injection(node, file_path, lines, findings, lang)
        cls._check_eval_injection(node, file_path, lines, findings, lang)
        cls._check_hardcoded_secrets(node, file_path, lines, findings, lang)
        cls._check_weak_crypto(node, file_path, lines, findings, lang)
        cls._check_insecure_deserialization(node, file_path, lines, findings, lang)
        cls._check_sql_injection(node, file_path, lines, findings, lang)
        cls._check_path_traversal(node, file_path, lines, findings, lang)

        for child in node.children:
            cls._visit_node(child, file_path, lines, findings, lang)

    @classmethod
    def _create_finding(
        cls,
        cwe_id: str,
        file_path: str,
        start_line: int,
        end_line: int,
        lines: list[str],
        custom_message: str | None = None,
        lang: str = "python",
    ) -> SecurityFinding:
        rule_spec = RULES_CATALOG[cwe_id]

        s_idx = max(0, start_line - 1)
        e_idx = min(len(lines), end_line)
        snippet = "\n".join(lines[s_idx:e_idx]).strip()

        code_hash = _calc_code_hash(snippet)
        finding_id = f"SEC-{cwe_id}-{code_hash[:12]}"

        return SecurityFinding(
            id=finding_id,
            rule_id=f"{file_path.split('.')[-1]}:{cwe_id}",
            cwe_id=cwe_id,
            category=FindingCategory.AST_PATTERN,
            severity=rule_spec.severity,
            title=rule_spec.title,
            message=custom_message or rule_spec.explanation,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            code_hash=code_hash,
            non_compliant_snippet=snippet,
            compliant_snippet=rule_spec.get_compliant_example(lang),
            remediation_guidance=rule_spec.explanation,
        )

    # --- Rule 1: Command Injection (CWE-78) ---
    @classmethod
    def _check_command_injection(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        if lang == "python":
            if node.type == "call":
                fn_node = node.child_by_field_name("function")
                args_node = node.child_by_field_name("arguments")
                if not fn_node or not args_node:
                    return

                fn_text = fn_node.text.decode("utf-8", errors="replace") if fn_node.text else ""
                if "subprocess" in fn_text or "system" in fn_text or fn_text == "popen":
                    if fn_text in ("os.system", "system"):
                        findings.append(
                            cls._create_finding(
                                "CWE-78",
                                file_path,
                                node.start_point[0] + 1,
                                node.end_point[0] + 1,
                                lines,
                                "Direct 'os.system()' call detected. Use subprocess.run().",
                            )
                        )
                        return

                    for arg in args_node.children:
                        if arg.type == "keyword_argument":
                            k = arg.child_by_field_name("name")
                            v = arg.child_by_field_name("value")
                            if k and v and k.text == b"shell" and v.text == b"True":
                                findings.append(
                                    cls._create_finding(
                                        "CWE-78",
                                        file_path,
                                        node.start_point[0] + 1,
                                        node.end_point[0] + 1,
                                        lines,
                                        "Subprocess executed with 'shell=True' risking injection.",
                                    )
                                )
                                return
        elif lang in _JS_LANGS and node.type == "call_expression":
            fn_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if fn_node and args_node:
                fn_text = fn_node.text.decode("utf-8", errors="replace") if fn_node.text else ""
                if fn_text.split(".")[-1] in ("exec", "execSync") and (
                    fn_text in ("exec", "execSync")
                    or "child_process" in fn_text
                    or "cp." in fn_text
                ):
                    first_arg = next(
                        (c for c in args_node.children if c.type not in ("(", ")", ",")),
                        None,
                    )
                    if first_arg and not _is_static_str_expr(first_arg):
                        findings.append(
                            cls._create_finding(
                                "CWE-78",
                                file_path,
                                node.start_point[0] + 1,
                                node.end_point[0] + 1,
                                lines,
                                f"Call to '{fn_text}()' uses a shell. Use 'execFile'.",
                                lang=lang,
                            )
                        )
        elif lang == "go" and node.type == "call_expression":
            fn_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if fn_node and args_node and fn_node.text in (b"exec.Command", b"exec.CommandContext"):
                args_txt = (
                    args_node.text.decode("utf-8", errors="replace") if args_node.text else ""
                )
                if any(s in args_txt for s in ('"sh"', '"/bin/sh"', '"bash"', '"cmd"')) and (
                    '"-c"' in args_txt or '"/C"' in args_txt
                ):
                    findings.append(
                        cls._create_finding(
                            "CWE-78",
                            file_path,
                            node.start_point[0] + 1,
                            node.end_point[0] + 1,
                            lines,
                            "Shell call via 'exec.Command' with '-c' risks command injection.",
                            lang=lang,
                        )
                    )
        elif lang == "rust" and node.type == "call_expression":
            txt = node.text.decode("utf-8", errors="replace") if node.text else ""
            if (
                "Command::new(" in txt
                and any(s in txt for s in ('"sh"', '"bash"', '"cmd"'))
                and ('.arg("-c")' in txt or '.arg("/C")' in txt)
                and (not node.parent or node.parent.type != "field_expression")
            ):
                findings.append(
                    cls._create_finding(
                        "CWE-78",
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        lines,
                        "Shell invocation via 'Command::new' with '-c' risks command injection.",
                        lang=lang,
                    )
                )

    # --- Rule 2: Code Injection / Eval (CWE-95) ---
    @classmethod
    def _check_eval_injection(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        if lang == "python":
            if node.type == "call":
                fn_node = node.child_by_field_name("function")
                if fn_node and fn_node.text in (b"eval", b"exec"):
                    findings.append(
                        cls._create_finding(
                            "CWE-95",
                            file_path,
                            node.start_point[0] + 1,
                            node.end_point[0] + 1,
                            lines,
                            f"Dangerous call to '{fn_node.text.decode()}' executes code.",
                        )
                    )
        elif lang in _JS_LANGS and node.type == "new_expression":
            ctor = node.child_by_field_name("constructor")
            if ctor and ctor.text == b"Function":
                findings.append(
                    cls._create_finding(
                        "CWE-95",
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        lines,
                        "'new Function()' compiles a string into executable JavaScript.",
                    )
                )
        elif lang in _JS_LANGS and node.type == "call_expression":
            fn_node = node.child_by_field_name("function")
            if fn_node and fn_node.text == b"eval":
                findings.append(
                    cls._create_finding(
                        "CWE-95",
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        lines,
                        "Dangerous call to 'eval()' can execute arbitrary JavaScript.",
                    )
                )

    # --- Rule 3: Hardcoded Secrets (CWE-798) ---
    @classmethod
    def _check_hardcoded_secrets(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        var_name: str | None = None
        val_str: str | None = None

        if lang == "python" and node.type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left and right and left.type == "identifier" and right.type == "string":
                var_name = left.text.decode("utf-8", errors="replace") if left.text else ""
                raw_val = right.text.decode("utf-8", errors="replace") if right.text else ""
                val_str = raw_val.strip("\"'")
        elif lang in ("javascript", "typescript") and node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            val_node = node.child_by_field_name("value")
            is_valid_decl = (
                name_node
                and val_node
                and name_node.type == "identifier"
                and val_node.type == "string"
            )
            if is_valid_decl and name_node and val_node:
                var_name = (
                    name_node.text.decode("utf-8", errors="replace") if name_node.text else ""
                )
                raw_val = val_node.text.decode("utf-8", errors="replace") if val_node.text else ""
                val_str = raw_val.strip("\"'`")

        if var_name and val_str and _SECRET_KEYWORD_RE.search(var_name) and len(val_str) >= 12:
            is_dummy = val_str.lower() in _DUMMY_SECRET_VALS or val_str.startswith(
                ("http://", "https://", "env:", "$")
            )
            if not is_dummy:
                findings.append(
                    cls._create_finding(
                        "CWE-798",
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        lines,
                        f"Hardcoded credential in '{var_name}'. Use environment variables.",
                    )
                )

    # --- Rule 4: Weak Cryptography (CWE-327) ---
    @classmethod
    def _check_weak_crypto(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        if lang == "python" and node.type == "call":
            fn_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if fn_node:
                fn_text = fn_node.text.decode("utf-8", errors="replace") if fn_node.text else ""
                args_txt = (
                    args_node.text.decode("utf-8", errors="replace")
                    if args_node and args_node.text
                    else ""
                )
                if fn_text in ("hashlib.md5", "hashlib.sha1") and "usedforsecurity=False" not in (
                    args_txt.replace(" ", "")
                ):
                    findings.append(
                        cls._create_finding(
                            "CWE-327",
                            file_path,
                            node.start_point[0] + 1,
                            node.end_point[0] + 1,
                            lines,
                            f"Weak hash algorithm '{fn_text}' has collision risks. Use sha256.",
                            lang=lang,
                        )
                    )
        elif lang in ("go", "rust") and node.type == "call_expression":
            fn_node = node.child_by_field_name("function")
            fn_text = (
                fn_node.text.decode("utf-8", errors="replace") if fn_node and fn_node.text else ""
            )
            if fn_text in ("md5.New", "md5.Sum", "sha1.New", "sha1.Sum", "Md5::new", "Sha1::new"):
                findings.append(
                    cls._create_finding(
                        "CWE-327",
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        lines,
                        f"Weak hash algorithm '{fn_text}' has collision risks. Use SHA-256.",
                        lang=lang,
                    )
                )

    # --- Rule 5: Insecure Deserialization (CWE-502) ---
    @classmethod
    def _check_insecure_deserialization(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        if lang == "python" and node.type == "call":
            fn_node = node.child_by_field_name("function")
            if fn_node:
                fn_text = fn_node.text.decode("utf-8", errors="replace") if fn_node.text else ""
                if fn_text in ("pickle.loads", "pickle.load", "_pickle.loads"):
                    findings.append(
                        cls._create_finding(
                            "CWE-502",
                            file_path,
                            node.start_point[0] + 1,
                            node.end_point[0] + 1,
                            lines,
                            "Call to 'pickle.loads()' allows arbitrary code execution. Use JSON.",
                        )
                    )

    # --- Rule 6: SQL Injection (CWE-89) ---
    @classmethod
    def _check_sql_injection(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        if node.type not in ("call", "call_expression"):
            return
        fn_node = node.child_by_field_name("function")
        args_node = node.child_by_field_name("arguments")
        if not fn_node or not args_node or not fn_node.text:
            return
        fn_text = fn_node.text.decode("utf-8", errors="replace")
        if not fn_text.endswith(
            (".execute", ".executemany", ".raw", ".query", ".Query", ".QueryRow", ".Exec")
        ):
            return
        first_arg = next(
            (c for c in args_node.children if c.type not in ("(", ")", ",")),
            None,
        )
        if not first_arg or not first_arg.text:
            return
        arg_text = first_arg.text.decode("utf-8", errors="replace")
        has_interp = (
            first_arg.type in ("binary_operator", "binary_expression", "template_string")
            or any(c.type == "interpolation" for c in first_arg.children)
            or (first_arg.type == "string" and arg_text.startswith(("f'", 'f"', "F'", 'F"')))
            or ".format(" in arg_text
            or "fmt.Sprintf(" in arg_text
        )
        if has_interp and re.search(r"(?i)\b(SELECT|INSERT|UPDATE|DELETE|DROP)\b", arg_text):
            interps = re.findall(r"\{([^{}]+)\}", arg_text)
            if interps and all(i.strip() in _SAFE_SQL_INTERPS for i in interps):
                return
            findings.append(
                cls._create_finding(
                    "CWE-89",
                    file_path,
                    node.start_point[0] + 1,
                    node.end_point[0] + 1,
                    lines,
                    f"Dynamic SQL query in '{fn_text}()'. Use parameterized queries.",
                    lang=lang,
                )
            )

    # --- Rule 7: Path Traversal (CWE-22) ---
    @classmethod
    def _check_path_traversal(
        cls,
        node: Node,
        file_path: str,
        lines: list[str],
        findings: list[SecurityFinding],
        lang: str,
    ) -> None:
        if lang == "python" and node.type == "call":
            fn_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if not fn_node or not args_node or not fn_node.text:
                return
            fn_text = fn_node.text.decode("utf-8", errors="replace")
            if fn_text not in ("open", "os.remove", "os.unlink", "shutil.rmtree", "send_file"):
                return
            first_arg = next(
                (c for c in args_node.children if c.type not in ("(", ")", ",")),
                None,
            )
            if not first_arg or not first_arg.text:
                return
            arg_text = first_arg.text.decode("utf-8", errors="replace")
            is_fstr = any(c.type == "interpolation" for c in first_arg.children) or (
                first_arg.type == "string" and arg_text.startswith(("f'", 'f"'))
            )
            if is_fstr and ("/" in arg_text or "\\" in arg_text):
                findings.append(
                    cls._create_finding(
                        "CWE-22",
                        file_path,
                        node.start_point[0] + 1,
                        node.end_point[0] + 1,
                        lines,
                        f"Unvalidated dynamic path in '{fn_text}()'. Validate via Path.resolve().",
                    )
                )
