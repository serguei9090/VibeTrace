"""Vibe Bundle Service: One-shot budgeted task context packing for AI pair programming.

Combines architectural docs, surgical code slice, caller blast-radius guard,
and sibling test patterns into a compact token-bounded payload (<= 1,500 tokens).
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from vibetrace.docs.schemas import DocChunkItem
from vibetrace.docs.service import DocSearchService
from vibetrace.indexer.db import IndexerDB
from vibetrace.services.caller_guard import CallerGuardReport, CallerGuardService
from vibetrace.services.hybrid_search import hybrid_search
from vibetrace.services.trace_service import CodeSliceResult, get_code_slice, read_file_lines

_EXT_LANG_MAP: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".sql": "sql",
    ".html": "html",
    ".css": "css",
    ".json": "json",
    ".md": "markdown",
}


def _get_lang_for_file(file_path: str | None) -> str:
    """Resolve markdown code fence language identifier from file extension."""
    if not file_path:
        return "text"
    suffix = Path(file_path).suffix.lower()
    return _EXT_LANG_MAP.get(suffix, "text")


def estimate_tokens(text: str) -> int:
    """Conservative token estimation with safety margin (~3.5 chars per token for code/prose)."""
    if not text:
        return 0
    # Conservative divisor (3.5 chars/token) to prevent LLM context overflows
    return max(1, int(len(text) / 3.5))


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
            code_slice_res.source_code = truncate_to_tokens(code_slice_res.source_code, code_budget)

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
            raw_docs = DocSearchService.search(
                query=clean_intent,
                project_root=root,
                limit=2,
                scope="local",
            )
            # If local docs had no results, search global
            if not raw_docs:
                raw_docs = DocSearchService.search(
                    query=clean_intent,
                    project_root=root,
                    limit=2,
                    scope="global",
                )
            seen_doc_keys: set[tuple[str, str]] = set()
            for d in raw_docs:
                d_key = (d.file_path, d.content.strip()[:120])
                if d_key not in seen_doc_keys:
                    seen_doc_keys.add(d_key)
                    doc_items.append(d)
        except Exception:
            doc_items = []

        # 4. Sibling Test Context (~20% of budget)
        test_budget = int(budget * 0.20)
        test_ctx = cls._find_sibling_test(db, root, resolved_symbol, target_f, test_budget)

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

        # Strategy B: Find matching test file by naming convention across polyglot ecosystems
        if target_file:
            tf = Path(target_file)
            stem = tf.stem
            suffix = tf.suffix.lower()
            parent_rel = tf.parent.as_posix()
            if parent_rel == ".":
                parent_rel = ""

            candidates: list[str] = []
            test_patterns: list[tuple[str, str]] = []

            if suffix == ".py":
                candidates = [
                    f"tests/test_{stem}.py",
                    f"tests/unit/test_{stem}.py",
                    f"tests/{stem}_test.py",
                    f"test_{stem}.py",
                ]
                test_patterns = [(r"^def (test_[a-zA-Z0-9_]+)", "test_suite")]
            elif suffix in (".ts", ".tsx", ".js", ".jsx"):
                candidates = [
                    f"{parent_rel}/{stem}.test{suffix}" if parent_rel else f"{stem}.test{suffix}",
                    f"{parent_rel}/{stem}.spec{suffix}" if parent_rel else f"{stem}.spec{suffix}",
                    f"tests/{stem}.test{suffix}",
                    f"tests/{stem}.spec{suffix}",
                    f"tests/{stem}.test.ts",
                    f"tests/{stem}.spec.ts",
                    f"__tests__/{stem}.test{suffix}",
                ]
                test_patterns = [
                    (r"(?:it|test)\s*\(\s*['\"]([^'\"]+)['\"]", "it_block"),
                    (r"describe\s*\(\s*['\"]([^'\"]+)['\"]", "describe_block"),
                ]
            elif suffix == ".go":
                candidates = [
                    f"{parent_rel}/{stem}_test.go" if parent_rel else f"{stem}_test.go",
                    f"{stem}_test.go",
                ]
                test_patterns = [(r"^func (Test[a-zA-Z0-9_]+)", "TestFunc")]
            elif suffix == ".rs":
                candidates = [
                    f"tests/{stem}_test.rs",
                    f"tests/{stem}.rs",
                    f"tests/test_{stem}.rs",
                ]
                test_patterns = [(r"fn (test_[a-zA-Z0-9_]+)", "test_func")]

            for cand in candidates:
                cand_path = root / cand
                if cand_path.is_file():
                    try:
                        content = cand_path.read_text(encoding="utf-8", errors="replace")
                        t_func = "test_suite"
                        for pattern, default_name in test_patterns:
                            m = re.search(pattern, content, flags=re.MULTILINE)
                            if m:
                                t_func = m.group(1)
                                break
                            t_func = default_name
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
            code_lang = _get_lang_for_file(code_slice.file_path)
            sections.append(f"```{code_lang}")
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
            test_lang = _get_lang_for_file(test_ctx.test_file)
            sections.append(f"```{test_lang}")
            sections.append(test_ctx.snippet.strip())
            sections.append("```")
            sections.append("")

        joined = "\n".join(sections)
        return truncate_to_tokens(joined, budget)


class AdaptiveSessionState(BaseModel):
    """Tracks active multi-turn exploration path and sliding token window."""

    model_config = ConfigDict(str_strip_whitespace=True)

    session_id: str
    root_symbol: str
    root_file: str | None = None
    current_focus: str
    current_file: str | None = None
    active_path: list[str] = Field(default_factory=list)
    history_stack: list[str] = Field(default_factory=list)
    pinned_symbols: list[str] = Field(default_factory=list)
    pruned_symbols: list[str] = Field(default_factory=list)
    token_budget: int = 1500
    turn_count: int = 1
    updated_at: str = Field(
        default_factory=lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


class AdaptiveBundleResult(BaseModel):
    """Result of an adaptive sliding-window session turn."""

    session: AdaptiveSessionState
    bundle: VibeBundle
    pruned_symbols: list[str] = Field(default_factory=list)


class AdaptiveSessionManager:
    """Manages session checkpoints and sliding call-graph context for coding agents."""

    @classmethod
    def get_session_dir(cls, root: Path) -> Path:
        s_dir = root / ".vibetrace" / "sessions"
        s_dir.mkdir(parents=True, exist_ok=True)
        return s_dir

    @classmethod
    def get_session_path(cls, root: Path, session_id: str) -> Path:
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ("-", "_"))
        return cls.get_session_dir(root) / f"{safe_id}.json"

    @classmethod
    def load_session(cls, root: Path, session_id: str) -> AdaptiveSessionState | None:
        p = cls.get_session_path(root, session_id)
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
            return AdaptiveSessionState(**raw)
        except Exception:
            return None

    @classmethod
    def save_session(cls, root: Path, state: AdaptiveSessionState) -> None:
        p = cls.get_session_path(root, state.session_id)
        p.write_text(state.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def reset_session(cls, root: Path, session_id: str) -> bool:
        p = cls.get_session_path(root, session_id)
        if p.is_file():
            try:
                p.unlink()
                return True
            except OSError:
                return False
        return False

    @classmethod
    def process_session_turn(
        cls,
        db: IndexerDB,
        root: Path,
        session_id: str,
        intent: str,
        focus_symbol: str | None = None,
        token_budget: int = 1500,
    ) -> AdaptiveBundleResult:
        """Executes a multi-turn adaptive context step with dynamic tree pruning."""
        state = cls.load_session(root, session_id)
        clean_intent = intent.strip() if intent else ""

        if state is None:
            # Turn 1: initialize new session
            resolved_init = focus_symbol
            if not resolved_init:
                hits = hybrid_search(db, clean_intent, limit=1)
                resolved_init = hits[0].name if hits else "root"

            state = AdaptiveSessionState(
                session_id=session_id,
                root_symbol=resolved_init,
                current_focus=resolved_init,
                active_path=[resolved_init],
                history_stack=[resolved_init],
                pinned_symbols=[resolved_init],
                token_budget=token_budget,
                turn_count=1,
            )
        else:
            state.turn_count += 1
            state.token_budget = token_budget
            new_target = focus_symbol
            if not new_target and clean_intent:
                hits = hybrid_search(db, clean_intent, limit=1)
                if hits:
                    new_target = hits[0].name

            if new_target and new_target != state.current_focus:
                if new_target not in state.active_path:
                    state.active_path.append(new_target)
                if new_target not in state.history_stack:
                    state.history_stack.append(new_target)

                # Prune: symbols in history that are not on active path and not pinned
                active_set = set(state.active_path) | set(state.pinned_symbols)
                newly_pruned = [
                    s for s in state.history_stack if s not in active_set and s != new_target
                ]
                state.pruned_symbols = newly_pruned
                state.current_focus = new_target

        state.updated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        # Assemble the base bundle for current focus
        bundle = VibeBundleService.assemble_bundle(
            db=db,
            root=root,
            intent=clean_intent or f"Inspect {state.current_focus}",
            target_symbol=state.current_focus,
            token_budget=token_budget,
        )

        # Prepend adaptive session lineage banner to prompt
        lineage_str = " -> ".join(state.active_path)
        pruned_str = (
            ", ".join(f"`{p}`" for p in state.pruned_symbols) if state.pruned_symbols else "None"
        )
        banner = (
            f"## 🧭 Adaptive Session Context: `{state.session_id}` (Turn {state.turn_count})\n"
            f"- **Root Anchor:** `{state.root_symbol}`\n"
            f"- **Active Call Lineage:** `{lineage_str}`\n"
            f"- **Pruned from Window:** {pruned_str}\n\n"
        )
        bundle.formatted_prompt = banner + bundle.formatted_prompt

        cls.save_session(root, state)
        return AdaptiveBundleResult(
            session=state, bundle=bundle, pruned_symbols=state.pruned_symbols
        )
