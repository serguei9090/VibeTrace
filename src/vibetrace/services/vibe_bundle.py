"""Vibe Bundle Service: One-shot budgeted task context packing for AI pair programming.

Combines architectural docs, surgical code slice, caller blast-radius guard,
and sibling test patterns into a compact token-bounded payload (<= 1,500 tokens).
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vibetrace.docs.schemas import DocChunkItem
from vibetrace.docs.service import DocSearchService
from vibetrace.indexer.db import IndexerDB
from vibetrace.services.caller_guard import CallerGuardReport, CallerGuardService
from vibetrace.services.hybrid_search import hybrid_search
from vibetrace.services.trace_service import CodeSliceResult, get_code_slice, read_file_lines


def estimate_tokens(text: str) -> int:
    """Rough token estimation (approx 3.8 - 4.0 chars per token for code/prose)."""
    if not text:
        return 0
    # Average across alphanumeric and punctuation tokens
    return max(1, len(text) // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    """Truncate text so estimated tokens do not exceed max_tokens."""
    if estimate_tokens(text) <= max_tokens:
        return text
    char_limit = max_tokens * 4
    truncated = text[:char_limit]
    # Try to break cleanly at last newline
    last_nl = truncated.rfind("\n")
    if last_nl > char_limit // 2:
        truncated = truncated[:last_nl]
    return truncated + "\n... [truncated to fit token budget]"


class SiblingTestContext(BaseModel):
    """Sibling test pattern or fixture for runnable verification."""

    model_config = ConfigDict(str_strip_whitespace=True)

    found: bool
    test_file: str | None = None
    test_function: str | None = None
    line_number: int | None = None
    snippet: str = ""


class VibeBundle(BaseModel):
    """Complete one-shot task context payload."""

    model_config = ConfigDict(str_strip_whitespace=True)

    intent: str
    target_symbol: str | None = None
    target_file: str | None = None
    token_budget: int = 1500
    estimated_tokens: int = 0
    doc_context: list[DocChunkItem] = Field(default_factory=list)
    code_slice: CodeSliceResult | None = None
    caller_guard: CallerGuardReport | None = None
    test_context: SiblingTestContext | None = None
    formatted_prompt: str = ""


class VibeBundleService:
    """Assembles one-shot task context for autonomous AI pair programming."""

    @classmethod
    def assemble_bundle(
        cls,
        db: IndexerDB,
        root: Path,
        intent: str,
        target_symbol: str | None = None,
        token_budget: int = 1500,
    ) -> VibeBundle:
        """Build an optimized context bundle bounded by the token budget."""
        budget = max(500, token_budget)
        clean_intent = intent.strip()

        # 1. Resolve Target Symbol & Code Slice
        # Allocate ~40% of budget to code slice
        code_budget = int(budget * 0.40)
        resolved_symbol = target_symbol
        code_slice_res: CodeSliceResult | None = None

        if not resolved_symbol:
            # Infer primary symbol from intent via hybrid search
            search_hits = hybrid_search(db, clean_intent, limit=3)
            if search_hits:
                resolved_symbol = search_hits[0].name
                # If hit has a file_path, use it
                target_file = search_hits[0].file_path
                code_slice_res = get_code_slice(
                    db,
                    root,
                    resolved_symbol,
                    file_path=target_file,
                    include_callees=True,
                )
        else:
            code_slice_res = get_code_slice(
                db,
                root,
                resolved_symbol,
                include_callees=True,
            )

        if code_slice_res and code_slice_res.found and code_slice_res.source_code:
            code_slice_res.source_code = truncate_to_tokens(
                code_slice_res.source_code, code_budget
            )

        # 2. Caller Guard (~15% of budget)
        caller_guard_res: CallerGuardReport | None = None
        target_f = code_slice_res.file_path if code_slice_res else None
        if resolved_symbol:
            caller_guard_res = CallerGuardService.get_caller_guard(
                db, root, resolved_symbol, file_path=target_f
            )

        # 3. Documentation Context (~25% of budget)
        doc_items: list[DocChunkItem] = []
        try:
            doc_items = DocSearchService.search(
                query=clean_intent,
                project_root=root,
                limit=2,
                scope="local",
            )
            # If local docs had no results, search global
            if not doc_items:
                doc_items = DocSearchService.search(
                    query=clean_intent,
                    project_root=root,
                    limit=2,
                    scope="global",
                )
        except Exception:
            doc_items = []

        # 4. Sibling Test Context (~20% of budget)
        test_budget = int(budget * 0.20)
        test_ctx = cls._find_sibling_test(
            db, root, resolved_symbol, target_f, test_budget
        )

        # 5. Format unified markdown and enforce global budget
        formatted_md = cls._format_bundle_prompt(
            intent=clean_intent,
            symbol=resolved_symbol,
            code_slice=code_slice_res,
            caller_guard=caller_guard_res,
            docs=doc_items,
            test_ctx=test_ctx,
            budget=budget,
        )

        actual_tokens = estimate_tokens(formatted_md)

        return VibeBundle(
            intent=clean_intent,
            target_symbol=resolved_symbol,
            target_file=code_slice_res.file_path if code_slice_res else None,
            token_budget=budget,
            estimated_tokens=actual_tokens,
            doc_context=doc_items,
            code_slice=code_slice_res,
            caller_guard=caller_guard_res,
            test_context=test_ctx,
            formatted_prompt=formatted_md,
        )

    @classmethod
    def _find_sibling_test(
        cls,
        db: IndexerDB,
        root: Path,
        symbol_name: str | None,
        target_file: str | None,
        test_budget: int,
    ) -> SiblingTestContext:
        """Find a related unit test calling the symbol or residing in sibling test file."""
        if not symbol_name and not target_file:
            return SiblingTestContext(found=False)

        # Strategy A: Check AST callers in test files
        if symbol_name:
            clean_sym = symbol_name.strip().removesuffix("()")
            nodes = db.find_nodes_by_name(clean_sym)
            if nodes:
                nid = nodes[0]["id"]
                callers = db.get_neighbors(nid, "up")
                for c in callers:
                    c_file = (c.get("file_path") or "").replace("\\", "/")
                    if "test" in c_file:
                        c_name = c.get("name") or ""
                        c_line = c.get("call_line") or c.get("start_line") or 1
                        # Grab small slice of test function
                        try:
                            snip_txt, _, _ = read_file_lines(
                                root, c_file, max(1, c_line - 2), c_line + 12
                            )
                            snip_trunc = truncate_to_tokens(snip_txt, test_budget)
                            return SiblingTestContext(
                                found=True,
                                test_file=c_file,
                                test_function=c_name,
                                line_number=c_line,
                                snippet=snip_trunc,
                            )
                        except Exception:
                            pass

        # Strategy B: Find matching test file by naming convention
        if target_file:
            stem = Path(target_file).stem
            candidates = [
                f"tests/test_{stem}.py",
                f"tests/unit/test_{stem}.py",
                f"tests/{stem}_test.py",
            ]
            for cand in candidates:
                cand_path = root / cand
                if cand_path.is_file():
                    try:
                        content = cand_path.read_text(encoding="utf-8", errors="replace")
                        # Find first test function
                        m = re.search(r"^def (test_[a-zA-Z0-9_]+)", content, flags=re.MULTILINE)
                        t_func = m.group(1) if m else "test_suite"
                        snip_trunc = truncate_to_tokens(content[:1500], test_budget)
                        return SiblingTestContext(
                            found=True,
                            test_file=cand,
                            test_function=t_func,
                            line_number=1,
                            snippet=snip_trunc,
                        )
                    except Exception:
                        pass

        return SiblingTestContext(found=False)

    @classmethod
    def _format_bundle_prompt(
        cls,
        intent: str,
        symbol: str | None,
        code_slice: CodeSliceResult | None,
        caller_guard: CallerGuardReport | None,
        docs: list[DocChunkItem],
        test_ctx: SiblingTestContext | None,
        budget: int,
    ) -> str:
        """Render the complete structured markdown bundle for AI reasoning."""
        sections: list[str] = [
            f"# Vibe Bundle: {intent}",
            f"> Context budget: {budget} tokens (optimized for one-shot zero-shot completion)",
            "",
        ]

        # 1. Caller Guard Blast Radius
        if caller_guard and caller_guard.found:
            sections.append("## 🛡️ Pre-Flight Caller Guard")
            sections.append(f"**Target:** `{caller_guard.symbol}` in `{caller_guard.file_path}`")
            sections.append(f"**Advisory:** {caller_guard.safety_advisory}")
            if caller_guard.callers:
                sections.append(f"**Active Callers ({caller_guard.total_callers}):**")
                for c in caller_guard.callers[:5]:
                    sections.append(f"- `{c.caller_name}` ({c.caller_file}:L{c.call_line or '?'})")
            sections.append("")

        # 2. Target Code Slice & Callee Contracts
        if code_slice and code_slice.found:
            sections.append("## 🧩 Target Code Implementation")
            loc = f"`{code_slice.file_path}:{code_slice.start_line}-{code_slice.end_line}`"
            sections.append(f"**Symbol:** `{code_slice.name}` ({loc})")
            if code_slice.signature:
                sections.append(f"**Signature:** `{code_slice.signature}`")
            sections.append("```python")
            sections.append(code_slice.source_code.strip())
            sections.append("```")
            if code_slice.callees:
                sections.append("**Direct Callee Contracts:**")
                for callee in code_slice.callees[:6]:
                    sig = f": `{callee.signature}`" if callee.signature else ""
                    sections.append(f"- `{callee.name}` ({callee.file_path}){sig}")
            sections.append("")

        # 3. Relevant Docs / Architecture Specs
        if docs:
            sections.append("## 📚 Architecture & Spec Docs")
            for d in docs:
                sections.append(f"### [{d.scope.upper()}:{d.catalog}] `{d.file_path}`")
                sections.append(d.content.strip())
            sections.append("")

        # 4. Sibling Test Pattern
        if test_ctx and test_ctx.found:
            sections.append("## 🧪 Sibling Test Pattern (Runnable Verification)")
            ref_str = f"**Test Reference:** `{test_ctx.test_file}` (`{test_ctx.test_function}`)"
            sections.append(ref_str)
            sections.append("```python")
            sections.append(test_ctx.snippet.strip())
            sections.append("```")
            sections.append("")

        joined = "\n".join(sections)
        return truncate_to_tokens(joined, budget)
