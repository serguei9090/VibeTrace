"""CSS and Web Accessibility (a11y) Quality Analyzer.

Evaluates CSS stylesheets for:
- css:S9423 / RSPEC-9423: Use of "!important" in property declarations
- css:S9449 / RSPEC-9449: "transition: all" or unspecified transition properties
- css:S9440 / RSPEC-9440: Animations/transforms without prefers-reduced-motion
- css:S7924 / RSPEC-7924: Minimal text color contrast against low-opacity background
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

_MEDIA_START_RE = re.compile(r"@media\s*([^{]+)\{?", re.IGNORECASE)
_RGBA_RE = re.compile(
    r"rgba\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*([0-9.]+)\s*\)",
    re.IGNORECASE,
)
_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_TIME_EASING_START_RE = re.compile(
    r"^(?:\d+(?:\.\d+)?(?:s|ms)|ease|linear|cubic-bezier)\b",
    re.IGNORECASE,
)


@dataclass
class _CSSDeclaration:
    property_name: str
    property_value: str
    line_number: int
    raw_line: str


@dataclass
class _CSSRuleBlock:
    selector: str
    start_line: int
    end_line: int
    media_query: str | None
    declarations: list[_CSSDeclaration]


def _strip_comments_preserving_lines(source: str) -> str:
    """Strips CSS /* ... */ comments while preserving exact line count and breaks."""

    def replacer(match: re.Match[str]) -> str:
        s = match.group(0)
        newlines = s.count("\n")
        return "\n" * newlines if newlines > 0 else " "

    return _COMMENT_RE.sub(replacer, source)


def _split_declarations(body: str) -> list[str]:
    """Splits CSS declarations by semicolon outside of quotes and parentheses."""
    parts: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    paren_depth = 0

    for ch in body:
        if ch == "'" and not in_double:
            in_single = not in_single
            current.append(ch)
        elif ch == '"' and not in_single:
            in_double = not in_double
            current.append(ch)
        elif not in_single and not in_double:
            if ch == "(":
                paren_depth += 1
                current.append(ch)
            elif ch == ")":
                paren_depth = max(0, paren_depth - 1)
                current.append(ch)
            elif ch == ";" and paren_depth == 0:
                part = "".join(current).strip()
                if part:
                    parts.append(part)
                current.clear()
            else:
                current.append(ch)
        else:
            current.append(ch)

    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def _parse_css_blocks(source_code: str) -> list[_CSSRuleBlock]:
    """Parses CSS into rule blocks handling single-line, multi-decl, and nested @media."""
    clean_css = _strip_comments_preserving_lines(source_code)
    blocks: list[_CSSRuleBlock] = []

    media_stack: list[str] = []
    i = 0
    n = len(clean_css)
    cur_line = 1
    in_single = False
    in_double = False
    token_start = 0
    token_start_line = 1

    while i < n:
        ch = clean_css[i]
        if ch == "\n":
            cur_line += 1

        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif not in_single and not in_double:
            if ch == "{":
                header = clean_css[token_start:i].strip()
                start_l = token_start_line

                # Find matching closing brace
                depth = 1
                body_start = i + 1
                j = body_start
                body_line = cur_line
                j_in_single = False
                j_in_double = False

                while j < n and depth > 0:
                    c2 = clean_css[j]
                    if c2 == "\n":
                        body_line += 1
                    if c2 == "'" and not j_in_double:
                        j_in_single = not j_in_single
                    elif c2 == '"' and not j_in_single:
                        j_in_double = not j_in_double
                    elif not j_in_single and not j_in_double:
                        if c2 == "{":
                            depth += 1
                        elif c2 == "}":
                            depth -= 1
                    j += 1

                end_l = body_line
                block_body = clean_css[body_start : j - 1]

                if header.startswith("@media"):
                    m = _MEDIA_START_RE.search(header)
                    media_query = m.group(1).strip() if m else header[6:].strip()
                    media_stack.append(media_query)
                    # Recursively parse rules inside the @media block
                    inner_blocks = _parse_css_blocks(block_body)
                    for ib in inner_blocks:
                        ib.media_query = media_query
                        ib.start_line += start_l - 1
                        ib.end_line += start_l - 1
                        for d in ib.declarations:
                            d.line_number += start_l - 1
                        blocks.append(ib)
                    media_stack.pop()
                elif header and not header.startswith("@"):
                    # Regular CSS rule block
                    raw_decls = _split_declarations(block_body)
                    parsed_decls: list[_CSSDeclaration] = []
                    for raw_d in raw_decls:
                        if ":" not in raw_d:
                            continue
                        prop, _, val = raw_d.partition(":")
                        prop_clean = prop.strip().lower()
                        val_clean = val.strip()
                        parsed_decls.append(
                            _CSSDeclaration(
                                property_name=prop_clean,
                                property_value=val_clean,
                                line_number=start_l,
                                raw_line=raw_d,
                            )
                        )
                    blocks.append(
                        _CSSRuleBlock(
                            selector=header,
                            start_line=start_l,
                            end_line=end_l,
                            media_query=media_stack[-1] if media_stack else None,
                            declarations=parsed_decls,
                        )
                    )

                i = j - 1
                token_start = j
                token_start_line = body_line
            elif ch == "}":
                token_start = i + 1
                token_start_line = cur_line
        i += 1

    return blocks


class CSSQualityAnalyzer:
    """Parses CSS and audits declarations for maintainability and accessibility defects."""

    def __init__(self, file_path: str, source_code: str) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.findings: list[QualityFinding] = []

    def _hash_snippet(self, snippet: str) -> str:
        return hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()

    def _make_finding_id(self, rule_id: str, line_no: int, desc: str) -> str:
        raw = f"{self.file_path}:{line_no}:{desc}"
        return f"QUAL-{rule_id.split(':')[-1]}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _has_unquoted_important(self, val: str) -> bool:
        """Checks if !important occurs as a keyword outside quotes."""
        unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "", val)
        return bool(re.search(r"!\s*important\b", unquoted, re.IGNORECASE))

    def _check_important(self, block: _CSSRuleBlock, decl: _CSSDeclaration) -> None:
        """css:S9423: Remove this '!important' and resolve the specificity conflict instead."""
        if not self._has_unquoted_important(decl.property_value):
            return

        fid = self._make_finding_id("S9423", decl.line_number, decl.property_name)
        spec = QUALITY_RULES_CATALOG.get("S9423")
        self.findings.append(
            QualityFinding(
                id=fid,
                rule_id="css:S9423",
                rspec_id="RSPEC-9423",
                category=QualityCategory.MAINTAINABILITY,
                severity=QualitySeverity.HIGH,
                title='Remove this "!important" and resolve the specificity conflict instead.',
                message=(
                    f"Declaration '{decl.property_name}' uses '!important'. Resolve "
                    "the selector specificity instead to avoid cascade override wars."
                ),
                file_path=self.file_path,
                start_line=decl.line_number,
                end_line=decl.line_number,
                symbol_name=block.selector,
                code_hash=self._hash_snippet(decl.raw_line),
                non_compliant_snippet=decl.raw_line.strip(),
                compliant_snippet=spec.compliant_example if spec else None,
                remediation_guidance=spec.explanation if spec else None,
            )
        )

    def _check_transition_property(self, block: _CSSRuleBlock, decl: _CSSDeclaration) -> None:
        """css:S9449: Specify which properties should transition."""
        prop = decl.property_name
        val = decl.property_value.lower().strip()

        is_transition_all = False
        if prop == "transition":
            if val == "all" or val.startswith(("all ", "all,")):
                is_transition_all = True
            elif _TIME_EASING_START_RE.search(val):
                is_transition_all = True
        elif prop == "transition-property":
            if val == "all" or "all" in [p.strip() for p in val.split(",")]:
                is_transition_all = True

        if is_transition_all:
            fid = self._make_finding_id("S9449", decl.line_number, "transition_all")
            spec = QUALITY_RULES_CATALOG.get("S9449")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="css:S9449",
                    rspec_id="RSPEC-9449",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.MEDIUM,
                    title=(
                        "Specify which properties should transition so future style changes "
                        "do not animate unexpectedly."
                    ),
                    message=(
                        f"Selector '{block.selector}' uses unspecified transition / 'all'. "
                        "Explicitly specify which properties to animate."
                    ),
                    file_path=self.file_path,
                    start_line=decl.line_number,
                    end_line=decl.line_number,
                    symbol_name=block.selector,
                    code_hash=self._hash_snippet(decl.raw_line),
                    non_compliant_snippet=decl.raw_line.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _check_reduced_motion(self, block: _CSSRuleBlock, decl: _CSSDeclaration) -> None:
        """css:S9440: Respect reduced-motion preferences to avoid triggering motion sensitivity."""
        media = (block.media_query or "").lower()
        if "prefers-reduced-motion" in media:
            return

        prop = decl.property_name
        val = decl.property_value.lower()

        is_anim = prop in ("animation", "animation-name")
        motion_keys = ("transform", "translate", "scale")
        is_motion_trans = (prop == "transition" and any(k in val for k in motion_keys)) or (
            prop == "transition-property" and any(k in val for k in motion_keys)
        )
        is_transform = prop in motion_keys

        if is_anim or is_motion_trans or is_transform:
            fid = self._make_finding_id("S9440", decl.line_number, prop)
            spec = QUALITY_RULES_CATALOG.get("S9440")
            self.findings.append(
                QualityFinding(
                    id=fid,
                    rule_id="css:S9440",
                    rspec_id="RSPEC-9440",
                    category=QualityCategory.ACCESSIBILITY,
                    severity=QualitySeverity.HIGH,
                    title=(
                        "Respect reduced-motion preferences here to avoid triggering "
                        "motion sensitivity."
                    ),
                    message=(
                        f"Movement animation/transition/transform in '{block.selector}' should "
                        "be guarded by prefers-reduced-motion to avoid discomfort."
                    ),
                    file_path=self.file_path,
                    start_line=decl.line_number,
                    end_line=decl.line_number,
                    symbol_name=block.selector,
                    code_hash=self._hash_snippet(decl.raw_line),
                    non_compliant_snippet=decl.raw_line.strip(),
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                )
            )

    def _check_contrast(self, block: _CSSRuleBlock) -> None:
        """css:S7924: Text does not meet the minimal contrast requirement with its background."""
        color_decl: _CSSDeclaration | None = None
        bg_decl: _CSSDeclaration | None = None

        for d in block.declarations:
            if d.property_name == "color":
                color_decl = d
            elif d.property_name in ("background", "background-color"):
                bg_decl = d

        if color_decl and bg_decl:
            is_light_text = color_decl.property_value.lower() in ("white", "#fff", "#ffffff")
            if is_light_text:
                m = _RGBA_RE.search(bg_decl.property_value)
                if m:
                    try:
                        alpha = float(m.group(1))
                        if alpha < 0.2:
                            fid = self._make_finding_id("S7924", color_decl.line_number, "contrast")
                            spec = QUALITY_RULES_CATALOG.get("S7924")
                            msg = (
                                f"Selector '{block.selector}' text '{color_decl.property_value}' "
                                f"on background '{bg_decl.property_value}' (alpha={alpha}) "
                                "fails WCAG 4.5:1 contrast."
                            )
                            self.findings.append(
                                QualityFinding(
                                    id=fid,
                                    rule_id="css:S7924",
                                    rspec_id="RSPEC-7924",
                                    category=QualityCategory.ACCESSIBILITY,
                                    severity=QualitySeverity.HIGH,
                                    title=(
                                        "Text does not meet the minimal contrast requirement "
                                        "with its background."
                                    ),
                                    message=msg,
                                    file_path=self.file_path,
                                    start_line=color_decl.line_number,
                                    end_line=color_decl.line_number,
                                    symbol_name=block.selector,
                                    code_hash=self._hash_snippet(color_decl.raw_line),
                                    non_compliant_snippet=color_decl.raw_line.strip(),
                                    compliant_snippet=spec.compliant_example if spec else None,
                                    remediation_guidance=spec.explanation if spec else None,
                                )
                            )
                    except ValueError:
                        pass

    def analyze(self) -> list[QualityFinding]:
        """Runs all CSS maintainability and a11y checks across parsed rule blocks."""
        blocks = _parse_css_blocks(self.source_code)
        for block in blocks:
            self._check_contrast(block)
            for decl in block.declarations:
                self._check_important(block, decl)
                self._check_transition_property(block, decl)
                self._check_reduced_motion(block, decl)

        return self.findings


def analyze_css_quality(file_path: str, source_code: str) -> list[QualityFinding]:
    """Analyzes a CSS file for specificity, maintainability, and accessibility issues."""
    analyzer = CSSQualityAnalyzer(file_path=file_path, source_code=source_code)
    return analyzer.analyze()
