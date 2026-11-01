"""TypeScript, TSX, and JSX Quality & Accessibility (a11y) Analyzer.

Analyzes TypeScript and JSX/TSX files via Tree-Sitter AST for:
- ts:S4323 / RSPEC-4323: Explicit 'any' type annotation or type assertion
- ts:S6544 / RSPEC-6544: Floating promises (unhandled / unawaited)
- jsx:S6852 / RSPEC-6852: <img> elements missing an 'alt' attribute
- jsx:S6848 / RSPEC-6848: Non-interactive elements with onClick missing role or keyboard handler
"""

from __future__ import annotations

import hashlib

import tree_sitter_typescript
from tree_sitter import Language, Node, Parser

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

_NON_INTERACTIVE_TAGS = {"div", "span", "p", "section", "article", "main", "header", "footer"}
_KEYBOARD_HANDLERS = {"onkeydown", "onkeyup", "onkeypress"}


class TypeScriptQualityAnalyzer:
    """AST analyzer evaluating TypeScript and JSX code smells and a11y compliance."""

    def __init__(self, file_path: str, source_code: str) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.findings: list[QualityFinding] = []

        is_tsx = self.file_path.endswith((".tsx", ".jsx"))
        lang_fn = (
            tree_sitter_typescript.language_tsx
            if is_tsx
            else tree_sitter_typescript.language_typescript
        )
        self.language = Language(lang_fn())
        self.parser = Parser(self.language)

    def _hash_snippet(self, snippet: str) -> str:
        return hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()

    def _make_finding_id(self, rule_id: str, line_no: int, desc: str) -> str:
        raw = f"{self.file_path}:{line_no}:{desc}"
        return f"QUAL-{rule_id.split(':')[-1]}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _get_node_text(self, node: Node) -> str:
        if node.text is None:
            return ""
        return node.text.decode("utf-8", errors="replace")

    def _check_explicit_any(self, node: Node) -> None:
        """ts:S4323: The 'any' type should not be used as an explicit type or type assertion."""
        if node.type == "predefined_type" and self._get_node_text(node) == "any":
            line_no = node.start_point[0] + 1
            snippet = self.source_lines[line_no - 1] if line_no <= len(self.source_lines) else ""
            fid = self._make_finding_id("S4323", line_no, "any_type")
            spec = QUALITY_RULES_CATALOG.get("S4323")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="ts:S4323",
                    rspec_id="RSPEC-4323",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.MEDIUM,
                    title="The 'any' type should not be used as an explicit type annotation.",
                    message=(
                        "Avoid using 'any' as it disables TypeScript's static type checker. "
                        "Use 'unknown', a generic parameter, or an explicit interface instead."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=node.end_point[0] + 1,
                    symbol_name="any",
                    code_hash=self._hash_snippet(snippet),
                    non_compliant_snippet=snippet.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _check_floating_promise(self, node: Node) -> None:
        """ts:S6544: Promises should not be left floating without await or catch."""
        if node.type != "expression_statement":
            return

        child = node.child(0)
        if child is None or child.type != "call_expression":
            return

        call_text = self._get_node_text(child)

        # Check for .then() without .catch()
        is_unhandled_then = ".then(" in call_text and ".catch(" not in call_text
        # Check for raw fetch() without await or void
        is_raw_fetch = call_text.startswith("fetch(") or "fetch(" in call_text.splitlines()[0]

        if is_unhandled_then or is_raw_fetch:
            line_no = node.start_point[0] + 1
            snippet = self.source_lines[line_no - 1] if line_no <= len(self.source_lines) else ""
            fid = self._make_finding_id("S6544", line_no, "floating_promise")
            spec = QUALITY_RULES_CATALOG.get("S6544")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="ts:S6544",
                    rspec_id="RSPEC-6544",
                    category=QualityCategory.RELIABILITY,
                    severity=QualitySeverity.HIGH,
                    title="Promises should not be left floating without await, catch, or void.",
                    message=(
                        f"Promise execution '{call_text[:50]}...' is unhandled. "
                        "Await the promise or append a '.catch()' error handler."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=node.end_point[0] + 1,
                    symbol_name="Promise",
                    code_hash=self._hash_snippet(snippet),
                    non_compliant_snippet=snippet.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _extract_jsx_attributes(self, element_node: Node) -> dict[str, str]:
        """Extracts key-value pairs of attributes in a JSX opening or self-closing element."""
        attrs: dict[str, str] = {}
        for child in element_node.children:
            if child.type != "jsx_attribute":
                continue
            prop_name = ""
            prop_val = ""
            for attr_child in child.children:
                if attr_child.type == "property_identifier":
                    prop_name = self._get_node_text(attr_child).lower()
                elif attr_child.type in ("string", "jsx_expression"):
                    prop_val = self._get_node_text(attr_child).strip("\"'{ }")
            if prop_name:
                attrs[prop_name] = prop_val
        return attrs

    def _check_jsx_img_alt(self, tag_name: str, attrs: dict[str, str], node: Node) -> None:
        """jsx:S6852: All <img> elements must have an 'alt' attribute."""
        if tag_name != "img":
            return

        if "alt" not in attrs:
            line_no = node.start_point[0] + 1
            snippet = self.source_lines[line_no - 1] if line_no <= len(self.source_lines) else ""
            fid = self._make_finding_id("S6852", line_no, "img_missing_alt")
            spec = QUALITY_RULES_CATALOG.get("S6852")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="jsx:S6852",
                    rspec_id="RSPEC-6852",
                    category=QualityCategory.ACCESSIBILITY,
                    severity=QualitySeverity.HIGH,
                    title="All '<img>' elements must have an 'alt' attribute for accessibility.",
                    message=(
                        "Image element is missing an 'alt' description. Add an descriptive alt "
                        "text, or alt='' for purely decorative images."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=node.end_point[0] + 1,
                    symbol_name="<img />",
                    code_hash=self._hash_snippet(snippet),
                    non_compliant_snippet=snippet.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _check_jsx_click_events(self, tag_name: str, attrs: dict[str, str], node: Node) -> None:
        """jsx:S6848: Non-interactive elements with onClick must have role and keyboard handler."""
        if tag_name not in _NON_INTERACTIVE_TAGS:
            return

        if "onclick" not in attrs:
            return

        has_role = bool(attrs.get("role"))
        has_key_handler = any(h in attrs for h in _KEYBOARD_HANDLERS)

        if not has_role or not has_key_handler:
            line_no = node.start_point[0] + 1
            snippet = self.source_lines[line_no - 1] if line_no <= len(self.source_lines) else ""
            fid = self._make_finding_id("S6848", line_no, f"{tag_name}_onclick")
            spec = QUALITY_RULES_CATALOG.get("S6848")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="jsx:S6848",
                    rspec_id="RSPEC-6848",
                    category=QualityCategory.ACCESSIBILITY,
                    severity=QualitySeverity.HIGH,
                    title=(
                        f"Non-interactive '<{tag_name}>' with 'onClick' must have an accessible "
                        "role and keyboard listener."
                    ),
                    message=(
                        f"Non-interactive element '<{tag_name}>' has an onClick handler but lacks "
                        "a role attribute or keyboard handler (onKeyDown). Use a <button> instead "
                        "or provide role='button' and onKeyDown."
                    ),
                    file_path=self.file_path,
                    start_line=line_no,
                    end_line=node.end_point[0] + 1,
                    symbol_name=f"<{tag_name}>",
                    code_hash=self._hash_snippet(snippet),
                    non_compliant_snippet=snippet.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _check_jsx_element(self, node: Node) -> None:
        """Dispatches JSX accessibility checks on opening and self-closing elements."""
        if node.type not in ("jsx_opening_element", "jsx_self_closing_element"):
            return

        tag_name = ""
        for child in node.children:
            if child.type == "identifier":
                tag_name = self._get_node_text(child).lower()
                break

        if not tag_name:
            return

        attrs = self._extract_jsx_attributes(node)
        self._check_jsx_img_alt(tag_name, attrs, node)
        self._check_jsx_click_events(tag_name, attrs, node)

    def _walk(self, node: Node) -> None:
        self._check_explicit_any(node)
        self._check_floating_promise(node)
        self._check_jsx_element(node)

        for child in node.children:
            self._walk(child)

    def analyze(self) -> list[QualityFinding]:
        """Parses and traverses the TypeScript / TSX source tree."""
        tree = self.parser.parse(self.source_code.encode("utf-8"))
        self._walk(tree.root_node)
        return self.findings


def analyze_ts_quality(file_path: str, source_code: str) -> list[QualityFinding]:
    """Analyzes a TypeScript or JSX/TSX file for code smells, type escapes, and a11y issues."""
    analyzer = TypeScriptQualityAnalyzer(file_path=file_path, source_code=source_code)
    return analyzer.analyze()
