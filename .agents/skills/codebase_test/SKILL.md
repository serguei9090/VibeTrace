---
name: codebase_test
description: Comprehensive instructions for testing the VibeGraph indexing and API systems.
---

# Codebase Testing Skill

This skill provides a standard operating procedure for verifying the VibeGraph codebase, including its terminal-based indexing, MCP server tools, and API endpoints.

## Prerequisites
- Python 3.10+
- `uv` package manager installed
- Tree-sitter languages compiled (automatically handled by the project)

## 1. Terminal-Based Testing

### Core Indexing
Perform a full indexing of the current directory:
```bash
uv run python -m vibegraph.indexer.main --path .
```
Verify that `vibegraph.db` is created and contains data.

### Database Verification
You can use standard SQL tools to verify the index:
```bash
sqlite3 vibegraph.db "SELECT COUNT(*) FROM nodes;"
```

## 2. MCP Tool Testing

If you are using an MCP-compatible client (like Antigravity), you can test the following tools:

### Indexing via MCP
```
vibegraph_reindex_project(path=".")
```

### Structural Summary
```
vibegraph_get_structural_summary(file_path="src/vibegraph/indexer/parser.py")
```

### Call Stack Analysis
```
vibegraph_get_call_stack(node_name="extract", direction="both")
```

## 3. API Testing

### Starting the Server
```bash
uv run python -m vibegraph.server_api
```

### Verifying Endpoints
Test the `/graph` endpoint:
```bash
curl http://localhost:8000/graph
```

## 4. Reporting
After testing, a report should be generated documenting:
- Indexing success rate.
- Any errors or gaps in extraction.
- Response times for large codebases.
