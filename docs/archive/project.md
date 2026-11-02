VibeGraph: The Nervous System MCP

Status: Unified Project Blueprint
Target: 100% Vibe Coding in Antigravity
Philosophy: Structural Truth > Statistical Guessing

0. Project Vision & "The Why"

Existing code-indexing tools (like code-rag) often rely on Vector Search, which consumes massive RAM (4GB+) and treats code like a text document. VibeGraph is built on the premise that code is a circuit board. By using Tree-sitter for AST parsing and SQLite for relational storage, we create a "Nervous System" for the project that understands exactly how components are wired together.

The Problem it Solves:

Hallucinated Relations: RAG might "guess" a file is related based on similarity. VibeGraph knows it is related because of an explicit function call or import.

Context Overload: Instead of feeding the AI thousands of lines of "similar" code, VibeGraph provides a precise "Call Stack" and "Impact Map."

Resource Exhaustion: VibeGraph runs in <50MB of RAM, leaving the rest of your system free for compilation and runtime.

1. Project Architecture & Stack

Backend: Python 3.11+ (managed by uv).

Database: SQLite (Relational Graph Store).

Parser: Tree-sitter (High-speed, incremental AST extraction).

Interface: Model Context Protocol (MCP) Server.

Visualization: Vite + React + React Flow (Isometric Code Map).

Linting/Formatting: Ruff (Python), Biome (JS/TS/React).

2. Phase-by-Phase Execution Plan

Phase 0: Initialization & Strict Linting

Goal: Setup a high-performance workspace.

Initialize Git.

Create directory structure: src/indexer, src/mcp, src/web, plans/.

Configure ruff.toml and biome.json.

Setup uv virtual environment and install base dependencies (mcp, tree-sitter, fastapi).

Phase 1: The Indexer (The Nervous System Engine)

Goal: Extract the "Ground Truth" of the codebase.

Extraction Logic: Use tree-sitter-languages to detect definitions and call sites.

Database Sync: Implement a watcher that updates the SQLite DB in real-time as files are saved.

Schema Intelligence: Store not just the name, but the "Signature" (parameters/return types) to help the AI understand usage without opening the file.

Phase 2: The MCP Server (The AI Interface)

Goal: Give the AI "eyes" into the Nervous System.

Tools:

get_call_stack: Trace a function's ancestry and descendants.

impact_analysis: Calculate the "Blast Radius" (e.g., "If I change this API, which 5 frontend components break?").

get_structural_summary: A condensed map of the current file's logic.

Phase 3: Visualizer (The Map Room)

Goal: Human-readable architectural mastery.

UI: An interactive React Flow graph.

Feature: "Vibe-Sync"—as the AI works on a file, the graph automatically centers on that node, highlighting its connections.

Phase 4: Vibe Rules & Deployment

Goal: Automate the "Vibe Coding" workflow.

Create a .cursorrules / .antigravityrules file that strictly enforces "Graph-First" reasoning.

3. The "Nervous System" SQLite Schema

-- Represents "Entities" (Functions, Classes, Variables)
CREATE TABLE nodes (
    id TEXT PRIMARY KEY,           -- Unique hash of file_path + name
    name TEXT NOT NULL,
    kind TEXT CHECK(kind IN ('function', 'class', 'module', 'interface', 'variable')),
    file_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,                -- The code signature for quick reference
    docstring TEXT                 -- Extracted comments for semantic context
);

-- Represents "Neural Connections" (Calls, Imports, Inheritance)
CREATE TABLE edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT,             -- The caller/user
    to_node_id TEXT,               -- The callee/definition
    relation_type TEXT CHECK(relation_type IN ('calls', 'defines', 'inherits', 'references', 'imports')),
    FOREIGN KEY(from_node_id) REFERENCES nodes(id),
    FOREIGN KEY(to_node_id) REFERENCES nodes(id)
);


4. Antigravity AI Instructions (Copy/Paste this)

"I want to build VibeGraph based on this specification.

First, scaffold the project structure and setup Phase 0. Ensure Biome and Ruff are configured for 'strict' mode.

Create the SQLite database and the Python Tree-sitter indexer in Phase 1. Focus on the 'Definition vs Call' extraction logic.

Once the indexer is populating the DB, build the MCP server in Phase 2.

Finally, create the React Flow visualization in Phase 3.
Always maintain 100% type safety and use Ruff/Biome for every file generated."

5. Vibe Coding Protocol (.cursorrules)

# VibeGraph Workflow for AI Agents
- **Rule 1**: NEVER modify a function without calling `impact_analysis`.
- **Rule 2**: Use `get_call_stack` to find implementation details instead of searching the whole codebase.
- **Rule 3**: If the user asks 'how does X work?', consult the graph first to see the flow of data.
- **Rule 4**: Trust the Graph. If the graph shows a relation, treat it as ground truth even if the text search is ambiguous.
