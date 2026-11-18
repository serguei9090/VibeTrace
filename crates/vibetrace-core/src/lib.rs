//! VibeTrace Native Hybrid Core (`_rust_core`)
//!
//! Provides GIL-free parallel directory walking (`ignore`), incremental SHA-256 diffing,
//! and multi-core Tree-sitter AST extraction (`rayon`) via PyO3.

pub mod models;
pub mod parser;
pub mod vectors;
pub mod walker;

use models::{RustEdge, RustFileDelta, RustFileExtraction, RustNode, RustWorkspaceScanResult};
use pyo3::prelude::*;
use std::collections::HashMap;
use std::path::Path;

/// Return the version of the compiled native Rust engine.
#[pyfunction]
pub fn rust_engine_version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

/// Perform parallel directory traversal, `.gitignore` filtering, and SHA-256 hashing
/// across all CPU cores without holding the Python GIL.
#[pyfunction]
#[pyo3(signature = (target_path, project_root, known_hashes, extra_excludes=None, force=false))]
pub fn py_scan_and_hash_workspace(
    py: Python<'_>,
    target_path: String,
    project_root: String,
    known_hashes: HashMap<String, String>,
    extra_excludes: Option<Vec<String>>,
    force: bool,
) -> PyResult<RustWorkspaceScanResult> {
    let excludes = extra_excludes.unwrap_or_default();
    let res = py.allow_threads(move || {
        walker::scan_and_hash_workspace(
            Path::new(&target_path),
            Path::new(&project_root),
            &known_hashes,
            &excludes,
            force,
        )
    });
    Ok(res)
}

/// Extract AST symbols and edges in parallel across a batch of dirty files without holding the GIL.
#[pyfunction]
pub fn py_extract_batch_parallel(
    py: Python<'_>,
    deltas: Vec<RustFileDelta>,
) -> PyResult<Vec<RustFileExtraction>> {
    let res = py.allow_threads(move || parser::extract_batch_parallel(&deltas));
    Ok(res)
}

/// Compute deterministic L2-normalized feature-hashing embeddings in parallel without the GIL.
#[pyfunction]
pub fn py_embed_texts_parallel(
    py: Python<'_>,
    texts: Vec<String>,
    dimensions: usize,
) -> PyResult<Vec<Vec<f32>>> {
    let res = py.allow_threads(move || vectors::embed_texts_parallel(&texts, dimensions));
    Ok(res)
}

/// Search packed little-endian `f32` embedding blobs via parallel cosine similarity without the GIL.
#[pyfunction]
pub fn py_search_vectors_parallel(
    py: Python<'_>,
    query: Vec<f32>,
    candidates: Vec<(String, Vec<u8>)>,
    dimensions: usize,
    limit: usize,
) -> PyResult<Vec<(String, f32)>> {
    let res = py.allow_threads(move || {
        vectors::search_vectors_parallel(&query, &candidates, dimensions, limit)
    });
    Ok(res)
}

#[pymodule]
fn _rust_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(rust_engine_version, m)?)?;
    m.add_function(wrap_pyfunction!(py_scan_and_hash_workspace, m)?)?;
    m.add_function(wrap_pyfunction!(py_extract_batch_parallel, m)?)?;
    m.add_function(wrap_pyfunction!(py_embed_texts_parallel, m)?)?;
    m.add_function(wrap_pyfunction!(py_search_vectors_parallel, m)?)?;
    m.add_class::<RustNode>()?;
    m.add_class::<RustEdge>()?;
    m.add_class::<RustFileDelta>()?;
    m.add_class::<RustWorkspaceScanResult>()?;
    m.add_class::<RustFileExtraction>()?;
    Ok(())
}
