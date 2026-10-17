-- Files tracking for incremental content-hash indexing
CREATE TABLE IF NOT EXISTS indexed_files (
    file_path TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    updated_at INTEGER NOT NULL
);

-- Represents "Entities" (Functions, Classes, Variables, Methods)
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,           -- Unique hash of file_path + qualified_name
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,  -- e.g. 'ClassName.method_name' or 'function_name'
    parent_id TEXT,                -- Enclosing class or module node ID
    kind TEXT,                     -- Flexible: 'function', 'class', 'struct', 'trait', 'impl', 'variable', 'module', 'method', 'interface'
    file_path TEXT NOT NULL,
    start_line INTEGER,
    end_line INTEGER,
    signature TEXT,                -- The code signature for quick reference
    docstring TEXT,                -- Extracted comments for semantic context
    decorators TEXT,               -- JSON list of decorators (e.g. ["@mcp.tool"])
    visibility TEXT,               -- 'public', 'private', 'protected', 'exported'
    centrality REAL DEFAULT 0.0    -- In-degree log-normalized graph centrality score
);

-- Represents "Neural Connections" (Calls, Imports, Inheritance, References)
CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_node_id TEXT,             -- The caller/user
    to_node_id TEXT,               -- The callee/definition
    relation_type TEXT,            -- 'calls', 'defines', 'inherits', 'references', 'imports', 'implements'
    call_line INTEGER,             -- Exact 1-indexed line number where the call occurs
    FOREIGN KEY(from_node_id) REFERENCES nodes(id),
    FOREIGN KEY(to_node_id) REFERENCES nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_file_path ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified_name ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_node_id);

-- Full-Text Search Virtual Table (FTS5 BM25)
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    node_id UNINDEXED,
    name,
    qualified_name,
    signature,
    docstring,
    file_path
);

-- Vector Embeddings Table (EmbeddingGemma 2 256d Matryoshka float32 blobs)
CREATE TABLE IF NOT EXISTS node_embeddings (
    node_id TEXT PRIMARY KEY,
    embedding BLOB NOT NULL,
    dimensions INTEGER NOT NULL DEFAULT 256,
    model_id TEXT NOT NULL,
    FOREIGN KEY(node_id) REFERENCES nodes(id)
);

-- Indexer Metadata Key-Value Table (Git-SHA delta cache, timestamps)
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Saved Architectural Trace Queries (Map Room bookmarks & named flows)
CREATE TABLE IF NOT EXISTS saved_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    from_symbol TEXT NOT NULL,
    to_symbol TEXT NOT NULL,
    max_depth INTEGER DEFAULT 6,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

