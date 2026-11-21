# Rust Native Core (`crates/vibetrace-core`)

## Overview

Native PyO3 (`abi3-py310`) Rust extension module (`vibetrace._rust_core`) that eliminates Python GIL bottlenecks across two hot paths: parallel gitignore-aware directory walking and SHA-256 content hashing (`walker.rs`), and native EmbeddingGemma 2 Q8 ONNX inference with parallel cosine similarity search (`vectors.rs`).

## Interface

- [crates/vibetrace-core/src/lib.rs:rust_engine_version] - Returns compiled crate version string.
- [crates/vibetrace-core/src/lib.rs:py_scan_and_hash_workspace] - GIL-free parallel workspace scan returning dirty file deltas and deleted paths (`walker.rs`).
- [crates/vibetrace-core/src/lib.rs:py_embed_texts_parallel] - Native EmbeddingGemma 2 Q8 L2-normalized embedding inference via ONNX Runtime (`vectors.rs`).
- [crates/vibetrace-core/src/lib.rs:py_embedding_runtime_info] - Reports model cache state and active ONNX Runtime execution provider.
- [crates/vibetrace-core/src/lib.rs:py_search_vectors_parallel] - Parallel cosine similarity ranking over packed little-endian `f32` embedding blobs.
- [crates/vibetrace-core/src/models.rs:RustFileDelta] / [crates/vibetrace-core/src/models.rs:RustWorkspaceScanResult] - PyO3 structs shared across the FFI boundary.
- [crates/vibetrace-core/src/vectors.rs:resolve_model_dir] - Resolves the EmbeddingGemma 2 ONNX cache directory.

## Invariants

- Directory traversal disables ancestor `.gitignore` inheritance (`parents(false)`) so parent repository rules never hide files inside nested projects [crates/vibetrace-core/src/walker.rs].
- All heavy PyO3 entry points (`py_scan_and_hash_workspace`, `py_embed_texts_parallel`, `py_search_vectors_parallel`) release the Python GIL via `py.allow_threads` [crates/vibetrace-core/src/lib.rs].
- Embedding inference targets `onnx-community/embeddinggemma-2-ONNX` (`tokenizer.json`, `model_quantized.onnx`, `model_quantized.onnx_data`) with `MAX_SEQ_LEN = 512`, `MICRO_BATCH_SIZE = 16`, explicit `"gpu"` (DirectML on Windows without silent CPU fallback) or `"cpu"` device selection (rejecting `"auto"`), Matryoshka truncation, and L2 normalization [crates/vibetrace-core/src/vectors.rs].
- `resolve_model_dir` checks explicit `model_dir`, then `VIBETRACE_MODEL_DIR`, then `~/.vibetrace/models/embeddinggemma-2-onnx` [crates/vibetrace-core/src/vectors.rs:resolve_model_dir].

## Side Effects

- Downloads missing EmbeddingGemma 2 ONNX files from HuggingFace into the resolved model cache directory on first embedding call [crates/vibetrace-core/src/vectors.rs].
- Caches initialized `EmbeddingGemma2Engine` ONNX sessions and active provider metadata in process-global `ENGINE_CACHE` [crates/vibetrace-core/src/vectors.rs].
- Reads workspace source files in parallel via `rayon` during directory walks and hashing [crates/vibetrace-core/src/walker.rs].

## Verification

- Run: `cargo test` (from `crates/vibetrace-core`)
- Run: `uv run --no-sync python -m pytest tests/test_hybrid_search.py tests/test_vector_store.py`
