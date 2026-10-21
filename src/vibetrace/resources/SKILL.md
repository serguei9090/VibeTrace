---
name: vibetrace-navigator
description: Surgical codebase discovery, execution tracepath analysis, blast-radius verification, and token-efficient source slicing via VibeTrace CLI and MCP tools. Use this skill whenever exploring a repository, tracing how an entry point reaches a target function, checking callers before refactoring, or reading specific symbol definitions without loading entire files.
---

# VibeTrace Navigator Skill

VibeTrace indexes the repository's Tree-sitter AST, call/inheritance graph (with exact 1-indexed `call_line` numbers), SQLite FTS5 BM25 lexical index, and optional local `EmbeddingGemma 2` (256d Matryoshka) vectors in a single `vibetrace.db` file.

Always follow the **3-Step Surgical Workflow** (`Locate -> Trace/Impact -> Slice`) instead of blind regex grepping or reading entire 500-line files.

---

## Step 1: Locate Symbols (`search` / `summary`)

Find the exact symbol name, `qualified_name` (`ClassName.method_name`), file path, and `start_line..end_line` using Hybrid Reciprocal Rank Fusion (RRF):

### Via MCP Tool
- `vibetrace_semantic_search(query="<symbol or natural language intent>", limit=15)`
- `vibetrace_get_structural_summary(file_path="<path/to/file.py>")`

### Via CLI
```bash
vibetrace search "database connection pool"
vibetrace summary src/vibetrace/indexer/db.py
```

---

## Step 2: Trace Execution Paths & Assess Blast Radius (`trace` / `impact`)

Before modifying a function or debugging a multi-hop flow, trace the exact hop-by-hop execution chain (`Node_0 -> ... -> Node_k` with `:L<call_line>`) or compute the 3-level caller blast radius:

### Via MCP Tool
- `vibetrace_trace_path(from_symbol="<entry_point>", to_symbol="<target_symbol>", max_depth=6)`
- `vibetrace_impact_analysis(file_path="<path/to/file.py>")`
- `vibetrace_find_references(symbol_name="<ClassName.method_name>")`

### Via CLI
```bash
vibetrace trace main get_file_structure
vibetrace impact src/vibetrace/indexer/db.py
```

---

## Step 3: Surgical Source Read (`slice`)

Once you know the target symbol, read **only** its exact source lines (`start_line..end_line`) and direct outgoing callee contracts—saving 85–95% of context window tokens compared to reading whole files:

### Via MCP Tool
- `vibetrace_get_code_slice(symbol_name="IndexerDB.upsert_node", include_callees=True)`

### Via CLI
```bash
vibetrace slice IndexerDB.upsert_node
vibetrace slice extract --file src/vibetrace/indexer/parsers/python_parser.py
```

---

## Keeping the Index Fresh

VibeTrace uses SHA-256 content-hash tracking (`indexed_files`) so incremental re-indexing skips unchanged files in milliseconds:

```bash
vibetrace index .
# Optional: encode local EmbeddingGemma 2 (256d MRL) vectors
vibetrace index . --embed
```
