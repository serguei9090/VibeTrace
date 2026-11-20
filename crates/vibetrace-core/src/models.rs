//! Data structures shared across the Rust native workspace scanner and PyO3 bridge.

use pyo3::prelude::*;

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
