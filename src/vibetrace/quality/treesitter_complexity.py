"""Universal Tree-sitter AST Quality and Cognitive Complexity Analyzer.

Evaluates Sonar-compatible RSPEC quality rules across polyglot languages:
- RSPEC-3776: Cognitive Complexity exceeds threshold
- RSPEC-107: Method has too many parameters
- RSPEC-134: Control flow statements nested too deeply
- RSPEC-1066: Collapsible nested if statements
- RSPEC-1192: Duplicated string literals
- RSPEC-3358: Nested ternary operators should not be used
- RSPEC-3626: Redundant jump / return statement
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence
from typing import TYPE_CHECKING

from vibetrace.quality.complexity import FunctionComplexity
from vibetrace.quality.models import (
    QualityCategory,
    QualityFinding,
    QualitySeverity,
)
from vibetrace.quality.rules import QUALITY_RULES_CATALOG

if TYPE_CHECKING:
    from tree_sitter import Node

_CONTROL_FLOW_TYPES = {
    "if_statement",
    "if",
    "unless",
    "for_statement",
    "for",
    "while_statement",
    "while",
    "until",
    "do_statement",
    "foreach_statement",
    "for_in_statement",
    "switch_statement",
    "case",
    "match_statement",
    "match_expression",
    "loop_expression",
    "catch_clause",
    "rescue",
    "except_clause",
    "conditional_expression",
    "ternary",
}

_NESTING_INCREMENT_TYPES = {
    "if_statement",
    "if",
    "unless",
    "for_statement",
    "for",
    "while_statement",
    "while",
    "until",
    "do_statement",
    "foreach_statement",
    "for_in_statement",
    "switch_statement",
    "case",
    "match_statement",
    "match_expression",
    "loop_expression",
    "catch_clause",
    "rescue",
    "except_clause",
}

_FUNCTION_NODE_TYPES = {
    "function_definition",
    "method_declaration",
    "function_declaration",
    "method_definition",
    "method",
    "singleton_method",
    "arrow_function",
    "function_expression",
    "function_item",
}

_STRING_NODE_TYPES = {
    "string",
    "string_literal",
    "interpreted_string_literal",
    "raw_string_literal",
    "template_string",
    "line_string_literal",
}

_IMPORT_NODE_TYPES = {
    "import_statement",
    "import_declaration",
    "import_from_statement",
    "use_declaration",
    "include_statement",
    "require_call",
}


def compute_tree_cognitive_complexity(node: Node, nesting: int = 0) -> int:
    """Calculates Cognitive Complexity according to SonarSource formal specification.

    Rules:
    - Increments (+1) for breaks in linear flow: if, for, while, switch, catch, ternary.
    - Nesting penalty (+1 per nesting level) applied to control flow structures.
    - Nesting increases inside control flow blocks.
    """
    score = 0
    for child in node.children:
        child_type = child.type
        if child_type in _CONTROL_FLOW_TYPES:
            # Cognitive score: +1 plus current nesting depth
            score += 1 + nesting
            if child_type in _NESTING_INCREMENT_TYPES:
                score += compute_tree_cognitive_complexity(child, nesting=nesting + 1)
            else:
                score += compute_tree_cognitive_complexity(child, nesting=nesting)
        elif child_type in ("else_clause", "else_if_clause"):
            # else / elseif adds 1 without nesting penalty
            score += 1
            score += compute_tree_cognitive_complexity(child, nesting=nesting + 1)
        else:
            score += compute_tree_cognitive_complexity(child, nesting=nesting)
    return score


def analyze_treesitter_quality(
    root_node: Node,
    file_path: str,
    source_lines: Sequence[str],
    complexity_threshold: int = 15,
    language_prefix: str = "php",
) -> tuple[list[FunctionComplexity], list[QualityFinding]]:
    """Inspects a Tree-sitter parse tree for RSPEC-3776, S107, S134, S1066, S1192, S3358, S3626."""
    complexities: list[FunctionComplexity] = []
    findings: list[QualityFinding] = []
    literal_occurrences: dict[str, list[int]] = defaultdict(list)

    def _walk(node: Node, in_ternary: bool = False, nesting_depth: int = 0) -> None:
        current_nesting = nesting_depth
        if node.type in _NESTING_INCREMENT_TYPES:
            current_nesting = nesting_depth + 1
            # 6. Deep Control Flow Nesting Check (RSPEC-134, max 3 allowed)
            if current_nesting > 3:
                n_start = node.start_point[0] + 1
                n_end = node.end_point[0] + 1
                n_snippet = "\n".join(source_lines[n_start - 1 : n_end])
                n_hash = hashlib.sha256(n_snippet.strip().encode("utf-8")).hexdigest()
                raw_id = f"{file_path}:{n_start}:{node.type}:{current_nesting}"
                fid = f"QUAL-S134-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                spec = QUALITY_RULES_CATALOG.get("S134")
                findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id=f"{language_prefix}:S134",
                        rspec_id="RSPEC-134",
                        category=QualityCategory.MAINTAINABILITY,
                        severity=QualitySeverity.MEDIUM,
                        title=(
                            f"Control flow statement is nested too deeply ({current_nesting} > 3)."
                        ),
                        message=(
                            f"Reduce nesting level from {current_nesting} (maximum allowed is 3) "
                            "by returning early or extracting inner blocks to separate functions."
                        ),
                        file_path=file_path.replace("\\", "/"),
                        start_line=n_start,
                        end_line=n_end,
                        code_hash=n_hash,
                        metric_value=float(current_nesting),
                        metric_threshold=3.0,
                        non_compliant_snippet=n_snippet[:200],
                        compliant_snippet=spec.compliant_example if spec else None,
                        remediation_guidance=spec.explanation if spec else None,
                        status="UNAPPROVED",
                    )
                )

        if node.type in _FUNCTION_NODE_TYPES:
            # Functions reset local control-flow nesting depth to 0
            current_nesting = 0
            name = ""
            params: list[str] = []
            body_node: Node | None = None

            for c in node.children:
                if c.type in ("name", "identifier"):
                    name = (c.text or b"").decode("utf-8", errors="replace")
                elif c.type in (
                    "formal_parameters",
                    "parameters",
                    "parameter_list",
                    "method_parameters",
                ):
                    for p in c.children:
                        if p.type in ("simple_parameter", "parameter", "identifier"):
                            params.append((p.text or b"").decode("utf-8", errors="replace"))
                elif c.type in ("compound_statement", "block", "statement_block"):
                    body_node = c

            if not name:
                name = "anonymous"

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            snippet = "\n".join(source_lines[start_line - 1 : end_line])
            code_hash = hashlib.sha256(snippet.strip().encode("utf-8")).hexdigest()

            # 1. Measure Cognitive Complexity (RSPEC-3776)
            cog_score = compute_tree_cognitive_complexity(body_node or node)
            fc = FunctionComplexity(
                name=name,
                start_line=start_line,
                end_line=end_line,
                cognitive_complexity=cog_score,
                cyclomatic_complexity=1,
                code_hash=code_hash,
                snippet=snippet[:300] + ("..." if len(snippet) > 300 else ""),
            )
            complexities.append(fc)

            if cog_score > complexity_threshold:
                raw_id = f"{file_path}:{name}:{code_hash[:16]}"
                fid = f"QUAL-S3776-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                spec = QUALITY_RULES_CATALOG.get("S3776")
                findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id=f"{language_prefix}:S3776",
                        rspec_id="RSPEC-3776",
                        category=QualityCategory.COMPLEXITY,
                        severity=QualitySeverity.HIGH,
                        title=(
                            f"Refactor '{name}' to reduce its Cognitive Complexity from "
                            f"{cog_score} to the {complexity_threshold} allowed."
                        ),
                        message=(
                            f"Function '{name}' has Cognitive Complexity of {cog_score} "
                            f"(maximum allowed is {complexity_threshold})."
                        ),
                        file_path=file_path.replace("\\", "/"),
                        start_line=start_line,
                        end_line=end_line,
                        symbol_name=name,
                        code_hash=code_hash,
                        metric_value=float(cog_score),
                        metric_threshold=float(complexity_threshold),
                        non_compliant_snippet=fc.snippet,
                        compliant_snippet=spec.compliant_example if spec else None,
                        remediation_guidance=spec.explanation if spec else None,
                        status="UNAPPROVED",
                    )
                )

            # 2. Measure Parameter Bloat (RSPEC-107, max 7 allowed)
            if len(params) > 7:
                raw_id = f"{file_path}:{name}:params:{len(params)}"
                fid = f"QUAL-S107-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                spec = QUALITY_RULES_CATALOG.get("S107")
                findings.append(
                    QualityFinding(
                        id=fid,
                        rule_id=f"{language_prefix}:S107",
                        rspec_id="RSPEC-107",
                        category=QualityCategory.MAINTAINABILITY,
                        severity=QualitySeverity.MEDIUM,
                        title=f"Function '{name}' has {len(params)} parameters (max 7 allowed).",
                        message=(
                            f"Function '{name}' has {len(params)} parameters, which is greater "
                            "than the 7 authorized. Consider bundling into a parameter object."
                        ),
                        file_path=file_path.replace("\\", "/"),
                        start_line=start_line,
                        end_line=start_line,
                        symbol_name=name,
                        code_hash=code_hash,
                        metric_value=float(len(params)),
                        metric_threshold=7.0,
                        non_compliant_snippet=snippet.splitlines()[0] if snippet else "",
                        compliant_snippet=spec.compliant_example if spec else None,
                        remediation_guidance=spec.explanation if spec else None,
                        status="UNAPPROVED",
                    )
                )

            # 3. Redundant Return Check (RSPEC-3626)
            if body_node:
                stmts = [c for c in body_node.children if c.type not in ("{", "}", "comment")]
                if stmts:
                    last_stmt = stmts[-1]
                    if last_stmt.type == "return_statement":
                        # If return has no return value (only 'return' and ';')
                        tokens = [c.type for c in last_stmt.children if c.type not in (";",)]
                        if len(tokens) <= 1:
                            r_line = last_stmt.start_point[0] + 1
                            raw_id = f"{file_path}:{name}:redundant_return:{r_line}"
                            fid = f"QUAL-S3626-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                            findings.append(
                                QualityFinding(
                                    id=fid,
                                    rule_id=f"{language_prefix}:S3626",
                                    rspec_id="RSPEC-3626",
                                    category=QualityCategory.MAINTAINABILITY,
                                    severity=QualitySeverity.LOW,
                                    title="Remove redundant jump / return statement.",
                                    message=(
                                        "This return statement is at the end of the block "
                                        "and does not return a value; it is redundant."
                                    ),
                                    file_path=file_path.replace("\\", "/"),
                                    start_line=r_line,
                                    end_line=r_line,
                                    symbol_name=name,
                                    code_hash=code_hash,
                                    status="UNAPPROVED",
                                )
                            )

        # 4. Nested Ternary Check (RSPEC-3358)
        if node.type == "conditional_expression" and in_ternary:
            t_line = node.start_point[0] + 1
            t_snippet = "\n".join(source_lines[t_line - 1 : t_line])
            t_hash = hashlib.sha256(t_snippet.strip().encode("utf-8")).hexdigest()
            raw_id = f"{file_path}:nested_ternary:{t_line}:{node.start_point[1]}"
            fid = f"QUAL-S3358-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
            spec = QUALITY_RULES_CATALOG.get("S3358")
            findings.append(
                QualityFinding(
                    id=fid,
                    rule_id=f"{language_prefix}:S3358",
                    rspec_id="RSPEC-3358",
                    category=QualityCategory.MAINTAINABILITY,
                    severity=QualitySeverity.MEDIUM,
                    title="Extract nested ternary operation into independent statement.",
                    message=(
                        "Nested ternary operators should not be used as they impair "
                        "readability. Use if-else statements or extract into variables."
                    ),
                    file_path=file_path.replace("\\", "/"),
                    start_line=t_line,
                    end_line=t_line,
                    code_hash=t_hash,
                    non_compliant_snippet=t_snippet,
                    compliant_snippet=spec.compliant_example if spec else None,
                    remediation_guidance=spec.explanation if spec else None,
                    status="UNAPPROVED",
                )
            )

        # 5. Mergeable If Statement Check (RSPEC-1066)
        if node.type == "if_statement":
            body = node.child_by_field_name("body")
            else_node = node.child_by_field_name("alternative")
            if not else_node and body:
                inner_stmts = [c for c in body.children if c.type not in ("{", "}", "comment")]
                if len(inner_stmts) == 1 and inner_stmts[0].type == "if_statement":
                    inner_if = inner_stmts[0]
                    if not inner_if.child_by_field_name("alternative"):
                        i_line = inner_if.start_point[0] + 1
                        i_snippet = "\n".join(source_lines[i_line - 1 : i_line])
                        i_hash = hashlib.sha256(i_snippet.strip().encode("utf-8")).hexdigest()
                        raw_id = f"{file_path}:mergeable_if:{i_line}"
                        fid = f"QUAL-S1066-{hashlib.sha256(raw_id.encode()).hexdigest()[:12]}"
                        spec = QUALITY_RULES_CATALOG.get("S1066")
                        findings.append(
                            QualityFinding(
                                id=fid,
                                rule_id=f"{language_prefix}:S1066",
                                rspec_id="RSPEC-1066",
                                category=QualityCategory.MAINTAINABILITY,
                                severity=QualitySeverity.LOW,
                                title="Merge this if statement with the enclosing one.",
                                message=(
                                    "Consecutive nested if statements without an else clause "
                                    "can be merged using logical AND (&&)."
                                ),
                                file_path=file_path.replace("\\", "/"),
                                start_line=i_line,
                                end_line=i_line,
                                code_hash=i_hash,
                                non_compliant_snippet=i_snippet,
                                compliant_snippet=spec.compliant_example if spec else None,
                                remediation_guidance=spec.explanation if spec else None,
                                status="UNAPPROVED",
                            )
                        )

        # 7. Collect string literals for S1192
        if node.type in _STRING_NODE_TYPES:
            parent = node.parent
            if not (parent and parent.type in _IMPORT_NODE_TYPES):
                raw_bytes = node.text
                if raw_bytes:
                    raw_text = raw_bytes.decode("utf-8", errors="replace")
                    clean = raw_text
                    if len(clean) >= 2:
                        if clean[0] in ('"', "'", "`") and clean[-1] == clean[0]:
                            clean = clean[1:-1]
                        elif clean.startswith(('r"', 'b"', 'f"')) and clean.endswith('"'):
                            clean = clean[2:-1]
                    if (
                        len(clean) >= 8
                        and not clean.startswith(("\\", "^", "@", "$"))
                        and "\n" not in clean
                    ):
                        literal_occurrences[clean].append(node.start_point[0] + 1)

        # Traverse children
        child_is_ternary = in_ternary or (node.type == "conditional_expression")
        for child in node.children:
            _walk(child, in_ternary=child_is_ternary, nesting_depth=current_nesting)

    _walk(root_node)

    # Emit Duplicated String Literals (RSPEC-1192, threshold >= 3)
    spec_1192 = QUALITY_RULES_CATALOG.get("S1192")
    for literal, lines in sorted(literal_occurrences.items(), key=lambda x: -len(x[1])):
        count = len(lines)
        if count < 3:
            continue
        first_line = lines[0]
        escaped_literal = literal[:40] + ("..." if len(literal) > 40 else "")
        hash_id = hashlib.sha256(f"{file_path}:{literal}".encode()).hexdigest()[:12]
        line_preview = source_lines[first_line - 1] if 0 < first_line <= len(source_lines) else ""
        findings.append(
            QualityFinding(
                id=f"QUAL-S1192-{hash_id}",
                rule_id=f"{language_prefix}:S1192",
                rspec_id="RSPEC-1192",
                category=QualityCategory.CODE_SMELL,
                severity=QualitySeverity.LOW,
                title=(
                    f"Define a constant instead of duplicating this literal "
                    f"'{escaped_literal}' {count} times."
                ),
                message=(
                    f"String literal '{escaped_literal}' is duplicated {count} times "
                    f"at lines {', '.join(map(str, lines[:5]))}."
                ),
                file_path=file_path.replace("\\", "/"),
                start_line=first_line,
                end_line=first_line,
                symbol_name=None,
                code_hash=hashlib.sha256(f"{literal}:{count}".encode()).hexdigest(),
                non_compliant_snippet=line_preview.strip() or f'"{escaped_literal}"',
                compliant_snippet=f'const CONSTANT_NAME = "{escaped_literal}";',
                remediation_guidance=spec_1192.explanation if spec_1192 else None,
                status="UNAPPROVED",
            )
        )

    return complexities, findings
