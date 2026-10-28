---
name: vibetrace-navigator
description: Surgical codebase discovery, execution tracepath analysis, blast-radius verification, token-efficient source slicing, and documentation RAG via VibeTrace CLI and MCP tools. Use this skill whenever exploring a repository, consulting project or master documentation, tracing how an entry point reaches a target function, checking callers before refactoring, or reading specific symbol definitions without loading entire files.
---

# VibeTrace Navigator Skill

VibeTrace indexes the repository's Tree-sitter AST, call/inheritance graph (with exact 1-indexed `call_line` numbers), SQLite FTS5 BM25 lexical index, documentation RAG catalogs (.md, .txt, .pdf), and optional local `EmbeddingGemma 2` (256d Matryoshka) vectors in SQLite databases.

---

## ⚡ Supercharged 100% Vibecoding Workflow

When starting a coding task or refactoring, use the dedicated Vibecoding superpowers to complete tasks in one shot with zero broken callers:

### 1. One-Shot Task Bundle (`bundle`)
Get everything needed for a task—relevant architecture specs, target code implementation, callee contracts, caller blast radius, and sibling test fixtures—in a single token-budgeted payload (<= 1,500 tokens):

- **MCP Tool:** `vibetrace_get_vibe_bundle(intent="add auth token to middleware", token_budget=1500)`
- **CLI:** `uv run vibetrace bundle "add auth token to middleware" --budget 1500`

### 2. Pre-Flight Caller Guard (`guard`)
**Rule 1 Requirement:** Never modify a function or method signature without inspecting all incoming callers first. The Caller Guard checks every caller in the AST graph, highlights exact call lines, and computes an active blast-radius advisory:

- **MCP Tool:** `vibetrace_check_caller_guard(symbol_name="upsert_node")`
- **CLI:** `uv run vibetrace guard upsert_node`

### 3. Git Pre-Commit Blast-Radius Gate (`guard --staged`)
Verify that modified function signatures across all staged git changes do not break or orphan incoming callers:
- **CLI (Advisory Check):** `uv run vibetrace guard --staged`
- **CLI (Strict Pre-Commit Gate):** `uv run vibetrace guard --staged --strict`

---

## Standard 4-Step Surgical Workflow (`Docs -> Locate -> Trace/Impact -> Slice`)

## Step 0: Consult Project & Master Docs (`docs`)

Before modifying code or planning architecture, consult project-specific requirements, design specs, or global master tech-stack guidelines:

### Scoping & Cross-Contamination Guardrails
- **Default (`scope="local"`):** Searches **only** the current project's documentation catalogs defined in `.vibetrace.toml`. This prevents global generalities from contaminating project-specific truths.
- **Master Stack (`scope="global"`):** Explicitly queries your personal master documentation catalogs in `~/.vibetrace/config.toml` for standard stack playbooks.
- **Combined (`scope="all"`):** Searches both local and global documentation simultaneously using 2-stream hybrid Reciprocal Rank Fusion (RRF).
- **Answer Scope Tags:** Each returned chunk is tagged with `[LOCAL:catalog]` or `[GLOBAL:catalog]` so you always know the provenance of every document snippet.

### Via MCP Tool
- `vibetrace_search_docs(query="<architecture, API contract, or standard>", scope="local", limit=5)`
- `vibetrace_list_doc_catalogs()`

### Via CLI
```bash
# Add folders to local project catalog and index
uv run vibetrace docs catalog add core docs/
uv run vibetrace docs index

# Search documentation (defaults to local project docs)
uv run vibetrace docs search "authentication flow"
# Search master stack documentation
uv run vibetrace docs search "rust error handling pattern" --scope global
```

---

## Step 1: Locate Symbols (`search` / `summary`)

Find the exact symbol name, `qualified_name` (`ClassName.method_name`), file path, and `start_line..end_line` using Hybrid Reciprocal Rank Fusion (RRF):

### Via MCP Tool
- `vibetrace_semantic_search(query="<symbol or natural language intent>", limit=15)`
- `vibetrace_get_structural_summary(file_path="<path/to/file.py>")`

### Via CLI
```bash
uv run vibetrace search "database connection pool"
uv run vibetrace summary src/vibetrace/indexer/db.py
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
uv run vibetrace trace vibetrace_get_structural_summary get_file_structure
uv run vibetrace impact src/vibetrace/indexer/db.py
```

---

## Step 3: Surgical Source Read (`slice`)

Once you know the target symbol, read **only** its exact source lines (`start_line..end_line`) and direct outgoing callee contracts—saving 85–95% of context window tokens compared to reading whole files:

### Via MCP Tool
- `vibetrace_get_code_slice(symbol_name="IndexerDB.upsert_node", include_callees=True)`

### Via CLI
```bash
uv run vibetrace slice IndexerDB.upsert_node
uv run vibetrace slice extract --file src/vibetrace/indexer/parsers/python_parser.py
```

---

## Keeping the Index Fresh

VibeTrace uses SHA-256 content-hash tracking (`indexed_files`) so incremental re-indexing skips unchanged files in milliseconds:

```bash
uv run vibetrace index .
# Optional: encode local EmbeddingGemma 2 (256d MRL) vectors
uv run vibetrace index . --embed
```
