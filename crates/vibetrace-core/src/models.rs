//! Data structures shared across the Rust native workspace scanner, AST extractor, and PyO3 bridge.

use pyo3::prelude::*;
use std::collections::HashMap;

#[pyclass(get_all)]
#[derive(Debug, Clone, PartialEq)]
pub struct RustNode {
    pub id: String,
    pub name: String,
    pub qualified_name: String,
    pub parent_id: Option<String>,
    pub kind: String,
    pub file_path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub signature: Option<String>,
    pub docstring: Option<String>,
    pub decorators: Option<Vec<String>>,
    pub visibility: String,
    pub centrality: f64,
}

#[pyclass(get_all)]
#[derive(Debug, Clone, PartialEq)]
pub struct RustEdge {
    pub from_node_id: String,
    pub to_node_id: String,
    pub relation_type: String,
    pub call_line: Option<usize>,
    pub confidence: String,
}

#[pyclass(get_all)]
#[derive(Debug, Clone)]
pub struct RustFileDelta {
    pub rel_path: String,
    pub content_hash: String,
    pub source_bytes: Vec<u8>,
}

#[pyclass(get_all)]
#[derive(Debug, Clone)]
pub struct RustWorkspaceScanResult {
    pub dirty_files: Vec<RustFileDelta>,
    pub unchanged_files: Vec<String>,
    pub deleted_files: Vec<String>,
    pub total_seen: usize,
}

#[pyclass(get_all)]
#[derive(Debug, Clone)]
pub struct RustFileExtraction {
    pub rel_path: String,
    pub content_hash: String,
    pub nodes: Vec<RustNode>,
    pub edges: Vec<RustEdge>,
    pub imported_symbols: HashMap<String, (String, String)>,
    pub target_symbols: HashMap<String, (String, String)>,
}
