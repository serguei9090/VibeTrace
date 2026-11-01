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
from dataclasses import dataclass, field

from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

_MEDIA_START_RE = re.compile(r"@media\s*([^{]+)\{?", re.IGNORECASE)
_PROPERTY_RE = re.compile(r"^\s*([a-z0-9_-]+)\s*:\s*(.+?)\s*;?\s*$", re.IGNORECASE)
_FAINT_BG_RE = re.compile(r"rgba\s*\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*0?\.\d+\s*\)", re.IGNORECASE)


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


@dataclass
class _CSSBlockParser:
    source_lines: list[str]
    blocks: list[_CSSRuleBlock] = field(default_factory=list)
    current_media: str | None = None
    current_selector: str = ""
    current_decls: list[_CSSDeclaration] = field(default_factory=list)
    block_start: int = 1
    in_rule: bool = False
    pending_prop: str | None = None
    pending_val_parts: list[str] = field(default_factory=list)
    pending_start_line: int = 1
    pending_raw_lines: list[str] = field(default_factory=list)

    def _flush_pending_decl(self) -> None:
        if self.pending_prop is not None:
            self.current_decls.append(
                _CSSDeclaration(
                    property_name=self.pending_prop.lower(),
                    property_value=" ".join(self.pending_val_parts).strip(),
                    line_number=self.pending_start_line,
                    raw_line="\n".join(self.pending_raw_lines),
                )
            )
            self.pending_prop = None
            self.pending_val_parts.clear()
            self.pending_raw_lines.clear()

    def _handle_closing_brace(self, idx: int) -> None:
        if self.in_rule:
            self.blocks.append(
                _CSSRuleBlock(
                    selector=self.current_selector.strip(),
                    start_line=self.block_start,
                    end_line=idx,
                    media_query=self.current_media,
                    declarations=list(self.current_decls),
                )
            )
            self.in_rule = False
            self.current_selector = ""
            self.current_decls.clear()
            self.pending_prop = None
            self.pending_val_parts.clear()
            self.pending_raw_lines.clear()
        else:
            self.current_media = None

    def _handle_decl_line(self, line: str, stripped: str, idx: int) -> None:
        if self.pending_prop is not None:
            self.pending_val_parts.append(stripped.rstrip(";"))
            self.pending_raw_lines.append(line)
            if ";" in stripped:
                self._flush_pending_decl()
            return

        if ":" not in stripped:
            return

        prop_name, _, val_part = stripped.partition(":")
        prop_name = prop_name.strip()
        val_part = val_part.strip()
        if ";" in val_part:
            self.current_decls.append(
                _CSSDeclaration(
                    property_name=prop_name.lower(),
                    property_value=val_part.rstrip(";").strip(),
                    line_number=idx,
                    raw_line=line,
                )
            )
            return

        self.pending_prop = prop_name
        self.pending_val_parts = [val_part] if val_part else []
        self.pending_start_line = idx
        self.pending_raw_lines = [line]

    def parse(self) -> list[_CSSRuleBlock]:
        for idx, line in enumerate(self.source_lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            if stripped.startswith("@media"):
                if m := _MEDIA_START_RE.search(stripped):
                    self.current_media = m.group(1).strip()
                continue
            if "}" in stripped:
                self._handle_closing_brace(idx)
                continue
            if "{" in stripped:
                self.current_selector = stripped.split("{")[0].strip()
                self.block_start = idx
                self.in_rule = True
                continue
            if self.in_rule:
                self._handle_decl_line(line, stripped, idx)

        return self.blocks


class CSSQualityAnalyzer:
    """Parses CSS and audits declarations for maintainability and accessibility defects."""

    def __init__(self, file_path: str, source_code: str) -> None:
        self.file_path = file_path.replace("\\", "/")
        self.source_code = source_code
        self.source_lines = source_code.splitlines()
        self.findings: list[QualityFinding] = []

    def _hash_snippet(self, snippet: str) -> str:
        return hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()

    def _make_finding_id(self, rule_id: str, line_no: int, desc: str) -> str:
        raw = f"{self.file_path}:{line_no}:{desc}"
        return f"QUAL-{rule_id.split(':')[-1]}-{hashlib.sha256(raw.encode()).hexdigest()[:12]}"

    def _parse_blocks(self) -> list[_CSSRuleBlock]:
        """Tokenizes CSS into rule blocks with selector, media context, and declarations."""
        return _CSSBlockParser(source_lines=self.source_lines).parse()

    def _check_important(self, block: _CSSRuleBlock, decl: _CSSDeclaration) -> None:
        """css:S9423: Remove this '!important' and resolve the specificity conflict instead."""
        if "!important" not in decl.property_value.lower():
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
        val = decl.property_value.lower()

        if prop == "transition" and (val.startswith("all ") or val == "all"):
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
                        f"Selector '{block.selector}' uses 'transition: all'. Explicitly specify "
                        "which properties to animate (e.g. opacity, border-color)."
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
        has_reduced_motion = bool(
            block.media_query and "prefers-reduced-motion" in block.media_query.lower()
        )
        if has_reduced_motion:
            return

        is_anim = decl.property_name in ("animation", "animation-name")
        is_motion_trans = decl.property_name == "transition" and "transform" in decl.property_value

        if is_anim or is_motion_trans:
            fid = self._make_finding_id("S9440", decl.line_number, decl.property_name)
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
                        f"Movement animation/transition in '{block.selector}' should be wrapped "
                        "in @media (prefers-reduced-motion: no-preference) to avoid triggering "
                        "vestibular discomfort."
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
            # Low contrast pattern: light text over faint translucent bg (< 0.2 opacity)
            is_light_text = color_decl.property_value.lower() in ("white", "#fff", "#ffffff")
            if is_light_text and _FAINT_BG_RE.search(bg_decl.property_value):
                fid = self._make_finding_id("S7924", color_decl.line_number, "contrast")
                spec = QUALITY_RULES_CATALOG.get("S7924")
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
                        message=(
                            f"Selector '{block.selector}' has text '{color_decl.property_value}' "
                            f"on faint background '{bg_decl.property_value}', "
                            "failing WCAG 4.5:1 contrast."
                        ),
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

    def analyze(self) -> list[QualityFinding]:
        """Runs all CSS maintainability and a11y checks across parsed rule blocks."""
        blocks = self._parse_blocks()
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
