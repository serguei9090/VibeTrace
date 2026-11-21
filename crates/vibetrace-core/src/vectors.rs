//! Native EmbeddingGemma 2 (ONNX Runtime) vector engine and parallel cosine similarity search.

use ndarray::{Array2, Ix2, Ix3};
use ort::environment::Environment;
use ort::ep::CPU;
#[cfg(target_os = "windows")]
use ort::ep::{DirectML, ExecutionProvider};
use ort::session::{builder::GraphOptimizationLevel, Session, SessionInputValue};
use ort::value::{Tensor, TensorElementType, ValueType};
use rayon::prelude::*;
use std::collections::HashMap;
use std::fs;
use std::io::{self, BufWriter};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use tokenizers::{Tokenizer, TruncationParams};

const MODEL_REPO_ID: &str = "onnx-community/embeddinggemma-2-ONNX";
const MAX_SEQ_LEN: usize = 512;
const MICRO_BATCH_SIZE: usize = 16;

const TOKENIZER_FILE: &str = "tokenizer.json";
const ONNX_MODEL_FILE: &str = "model_quantized.onnx";
const ONNX_DATA_FILE: &str = "model_quantized.onnx_data";

const URL_TOKENIZER: &str =
    "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/tokenizer.json";
const URL_ONNX_MODEL: &str =
    "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/onnx/model_quantized.onnx";
const URL_ONNX_DATA: &str =
    "https://huggingface.co/onnx-community/embeddinggemma-2-ONNX/resolve/main/onnx/model_quantized.onnx_data";

const REQUIRED_MODEL_FILES: [(&str, &str); 3] = [
    (TOKENIZER_FILE, URL_TOKENIZER),
    (ONNX_MODEL_FILE, URL_ONNX_MODEL),
    (ONNX_DATA_FILE, URL_ONNX_DATA),
];

struct EmbeddingGemma2Engine {
    tokenizer: Tokenizer,
    pad_id: i64,
    session: Session,
    model_path: PathBuf,
    active_provider: String,
    cache_dir: PathBuf,
}

static ENGINE_CACHE: OnceLock<Mutex<HashMap<(PathBuf, String), EmbeddingGemma2Engine>>> =
    OnceLock::new();
static LAST_ACTIVE_PROVIDER: OnceLock<Mutex<HashMap<PathBuf, String>>> = OnceLock::new();

fn engine_cache() -> &'static Mutex<HashMap<(PathBuf, String), EmbeddingGemma2Engine>> {
    ENGINE_CACHE.get_or_init(|| Mutex::new(HashMap::new()))
}

fn last_active_provider_map() -> &'static Mutex<HashMap<PathBuf, String>> {
    LAST_ACTIVE_PROVIDER.get_or_init(|| Mutex::new(HashMap::new()))
}

fn user_home_dir() -> PathBuf {
    #[cfg(target_os = "windows")]
    {
        if let Some(p) = std::env::var_os("USERPROFILE").filter(|v| !v.is_empty()) {
            return PathBuf::from(p);
        }
    }
    if let Some(p) = std::env::var_os("HOME").filter(|v| !v.is_empty()) {
        return PathBuf::from(p);
    }
    std::env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

/// Resolve the EmbeddingGemma 2 ONNX model cache directory.
pub fn resolve_model_dir(model_dir: Option<&Path>) -> PathBuf {
    if let Some(dir) = model_dir {
        if !dir.as_os_str().is_empty() {
            return dir.to_path_buf();
        }
    }
    if let Some(env_dir) = std::env::var_os("VIBETRACE_MODEL_DIR").filter(|v| !v.is_empty()) {
        return PathBuf::from(env_dir);
    }
    user_home_dir()
        .join(".vibetrace")
        .join("models")
        .join("embeddinggemma-2-onnx")
}

fn normalize_device_request(device: &str) -> Result<&'static str, String> {
    match device.trim().to_ascii_lowercase().as_str() {
        "" | "gpu" | "cuda" | "directml" | "dml" => Ok("gpu"),
        "cpu" => Ok("cpu"),
        other => Err(format!(
            "Invalid compute device '{other}'. Only 'gpu' (default) or 'cpu' are supported."
        )),
    }
}

fn missing_model_files(cache_dir: &Path) -> Vec<(&'static str, &'static str)> {
    REQUIRED_MODEL_FILES
        .iter()
        .copied()
        .filter(|(name, _)| {
            let p = cache_dir.join(name);
            !p.is_file() || fs::metadata(&p).map(|m| m.len() == 0).unwrap_or(true)
        })
        .collect()
}

fn format_missing_model_error(cache_dir: &Path, reason: &str) -> String {
    format!(
        "EmbeddingGemma 2 Q8 ONNX model files are unavailable ({reason}).\n\
         No fallback hash vectorizer is configured. To install the model offline, \
         download these 3 files from {MODEL_REPO_ID} and place them directly inside:\n  {dir}\n\
         Required files:\n\
           1. {TOKENIZER_FILE} <- {URL_TOKENIZER}\n\
           2. {ONNX_MODEL_FILE} <- {URL_ONNX_MODEL}\n\
           3. {ONNX_DATA_FILE} <- {URL_ONNX_DATA}",
        dir = cache_dir.display(),
    )
}

fn is_offline_mode() -> bool {
    std::env::var("VIBETRACE_OFFLINE")
        .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
        .unwrap_or(false)
}

fn ensure_model_files(cache_dir: &Path) -> Result<(), String> {
    let missing = missing_model_files(cache_dir);
    if missing.is_empty() {
        return Ok(());
    }

    if is_offline_mode() {
        return Err(format_missing_model_error(
            cache_dir,
            "VIBETRACE_OFFLINE is enabled and cache is incomplete",
        ));
    }

    fs::create_dir_all(cache_dir).map_err(|e| {
        format_missing_model_error(
            cache_dir,
            &format!(
                "failed to create cache directory {}: {e}",
                cache_dir.display()
            ),
        )
    })?;

    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_secs(20))
        .timeout_read(Duration::from_secs(60))
        .timeout_write(Duration::from_secs(20))
        .build();

    for (filename, url) in missing {
        let final_path = cache_dir.join(filename);
        let part_path = cache_dir.join(format!("{filename}.part"));
        eprintln!("[vibetrace] Downloading {filename} from {url} ...");

        let download_result: Result<(), String> = (|| {
            let resp = agent
                .get(url)
                .call()
                .map_err(|e| format!("HTTP request failed: {e}"))?;
            let mut reader = resp.into_reader();
            let file = fs::File::create(&part_path)
                .map_err(|e| format!("failed to create {}: {e}", part_path.display()))?;
            let mut writer = BufWriter::new(file);
            let bytes = io::copy(&mut reader, &mut writer)
                .map_err(|e| format!("stream copy failed: {e}"))?;
            if bytes == 0 {
                return Err("downloaded 0 bytes".to_string());
            }
            use std::io::Write;
            writer.flush().map_err(|e| format!("flush failed: {e}"))?;
            drop(writer);
            if final_path.exists() {
                let _ = fs::remove_file(&final_path);
            }
            fs::rename(&part_path, &final_path)
                .map_err(|e| format!("failed to move {} into place: {e}", part_path.display()))?;
            Ok(())
        })();

        if let Err(err) = download_result {
            let _ = fs::remove_file(&part_path);
            return Err(format_missing_model_error(
                cache_dir,
                &format!("failed downloading {filename}: {err}"),
            ));
        }
    }

    Ok(())
}

static ORT_ENV: OnceLock<Result<Environment, String>> = OnceLock::new();

fn ort_env() -> Result<&'static Environment, String> {
    ORT_ENV
        .get_or_init(|| {
            ort::init()
                .with_name("vibetrace")
                .build()
                .map_err(|e| format!("ORT environment initialization failed: {e}"))
        })
        .as_ref()
        .map_err(Clone::clone)
}

fn cpu_thread_count() -> usize {
    std::thread::available_parallelism()
        .map(|n| n.get().clamp(1, 8))
        .unwrap_or(4)
}

fn build_cpu_session(model_path: &Path) -> Result<Session, String> {
    let env = ort_env()?;
    let threads = cpu_thread_count();
    Session::builder(env)
        .map_err(|e| format!("ORT SessionBuilder failed: {e}"))?
        .with_optimization_level(GraphOptimizationLevel::Level3)
        .map_err(|e| format!("ORT optimization config failed: {e}"))?
        .with_intra_threads(threads)
        .map_err(|e| format!("ORT intra-thread config failed: {e}"))?
        .with_execution_providers([CPU::default().build()])
        .map_err(|e| format!("ORT CPU EP registration failed: {e}"))?
        .commit_from_file(model_path)
        .map_err(|e| format!("Failed loading ONNX model {}: {e}", model_path.display()))
}

fn gpu_unavailable_error(detail: &str) -> String {
    format!(
        "GPU execution provider is not available ({detail}). Please switch to CPU (pass --device cpu or set device = \"cpu\" in .vibetrace.toml)."
    )
}

fn build_session(model_path: &Path, requested_device: &str) -> Result<(Session, String), String> {
    if requested_device == "cpu" {
        let sess = build_cpu_session(model_path)?;
        return Ok((sess, "cpu".to_string()));
    }

    #[cfg(target_os = "windows")]
    {
        if !DirectML::is_available().unwrap_or(false) {
            return Err(gpu_unavailable_error(
                "DirectML execution provider is unavailable",
            ));
        }
        let env = ort_env().map_err(|e| gpu_unavailable_error(&e))?;
        let dml_attempt: Result<Session, String> = (|| {
            Session::builder(env)
                .map_err(|e| format!("{e}"))?
                .with_optimization_level(GraphOptimizationLevel::Level3)
                .map_err(|e| format!("{e}"))?
                .with_memory_pattern(false)
                .map_err(|e| format!("{e}"))?
                .with_execution_providers([DirectML::default()
                    .with_device_id(0)
                    .build()
                    .error_on_failure()])
                .map_err(|e| format!("{e}"))?
                .commit_from_file(model_path)
                .map_err(|e| format!("{e}"))
        })();
        match dml_attempt {
            Ok(sess) => Ok((sess, "directml".to_string())),
            Err(detail) => Err(gpu_unavailable_error(&detail)),
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        let _ = model_path;
        Err(gpu_unavailable_error(
            "DirectML GPU acceleration is only supported on Windows in this build",
        ))
    }
}

impl EmbeddingGemma2Engine {
    fn load(cache_dir: PathBuf, requested_device: &str) -> Result<Self, String> {
        ensure_model_files(&cache_dir)?;

        let tok_path = cache_dir.join(TOKENIZER_FILE);
        let model_path = cache_dir.join(ONNX_MODEL_FILE);

        let mut tokenizer = Tokenizer::from_file(&tok_path)
            .map_err(|e| format!("Failed loading tokenizer {}: {e}", tok_path.display()))?;
        tokenizer
            .with_truncation(Some(TruncationParams {
                max_length: MAX_SEQ_LEN,
                ..Default::default()
            }))
            .map_err(|e| format!("Failed configuring tokenizer truncation: {e}"))?;

        let pad_id = tokenizer
            .token_to_id("<pad>")
            .or_else(|| tokenizer.token_to_id("[PAD]"))
            .unwrap_or(0) as i64;

        let (session, active_provider) = build_session(&model_path, requested_device)?;
        Ok(Self {
            tokenizer,
            pad_id,
            session,
            model_path,
            active_provider,
            cache_dir,
        })
    }

    fn extract_batch_outputs(
        outputs: &ort::session::SessionOutputs<'_>,
        attention_mask: &Array2<i64>,
        rows: usize,
        dimensions: usize,
    ) -> Result<Vec<Vec<f32>>, String> {
        if let Some(sent_val) = outputs.get("sentence_embedding") {
            let view = sent_val
                .try_extract_array::<f32>()
                .map_err(|e| format!("Failed extracting sentence_embedding: {e}"))?;
            let mat = view
                .into_dimensionality::<Ix2>()
                .map_err(|e| format!("Expected 2D sentence_embedding tensor: {e}"))?;
            let mut batch_vecs = Vec::with_capacity(rows);
            for r in 0..mat.nrows() {
                let row = mat.row(r);
                let mut vec = vec![0.0f32; dimensions];
                let copy_len = row.len().min(dimensions);
                for (dst, src) in vec[..copy_len].iter_mut().zip(row.iter()) {
                    *dst = *src;
                }
                l2_normalize_in_place(&mut vec);
                batch_vecs.push(vec);
            }
            return Ok(batch_vecs);
        }

        if let Some(lhs_val) = outputs.get("last_hidden_state") {
            let view = lhs_val
                .try_extract_array::<f32>()
                .map_err(|e| format!("Failed extracting last_hidden_state: {e}"))?;
            let tensor = view
                .into_dimensionality::<Ix3>()
                .map_err(|e| format!("Expected 3D last_hidden_state tensor: {e}"))?;
            let (_, seq_len, hidden_dim) = tensor.dim();
            let copy_dim = hidden_dim.min(dimensions);
            let mut batch_vecs = Vec::with_capacity(rows);
            for r in 0..rows {
                let mut pooled = vec![0.0f32; dimensions];
                let mut token_weight_sum = 0.0f32;
                for t in 0..seq_len {
                    let mask = attention_mask[[r, t]] as f32;
                    if mask <= 0.0 {
                        continue;
                    }
                    token_weight_sum += mask;
                    for d in 0..copy_dim {
                        pooled[d] += tensor[[r, t, d]] * mask;
                    }
                }
                if token_weight_sum > 0.0 {
                    let inv = token_weight_sum.recip();
                    for v in &mut pooled[..copy_dim] {
                        *v *= inv;
                    }
                }
                l2_normalize_in_place(&mut pooled);
                batch_vecs.push(pooled);
            }
            return Ok(batch_vecs);
        }

        Err(
            "ONNX model returned neither 'sentence_embedding' nor 'last_hidden_state' output tensor."
                .to_string(),
        )
    }

    fn build_session_inputs(
        session: &Session,
        input_ids: &Array2<i64>,
        attention_mask: &Array2<i64>,
    ) -> Result<Vec<(String, SessionInputValue<'static>)>, String> {
        let mut pairs: Vec<(String, SessionInputValue<'static>)> =
            Vec::with_capacity(session.inputs().len());

        for outlet in session.inputs() {
            let name = outlet.name();
            match name {
                "input_ids" => {
                    let t = Tensor::from_array(input_ids.clone())
                        .map_err(|e| format!("Failed creating input_ids tensor: {e}"))?;
                    pairs.push((name.to_string(), t.into()));
                }
                "attention_mask" => {
                    let t = Tensor::from_array(attention_mask.clone())
                        .map_err(|e| format!("Failed creating attention_mask tensor: {e}"))?;
                    pairs.push((name.to_string(), t.into()));
                }
                _ => {
                    // Multimodal exports (e.g. Gemma/EmbeddingGemma 2) declare empty optional
                    // feature tensors such as `image_features`, `video_features`, `audio_features`.
                    let (elem_ty, shape_vec): (TensorElementType, Vec<usize>) = match outlet.dtype()
                    {
                        ValueType::Tensor { ty, shape, .. } if !shape.is_empty() => {
                            let mut dims: Vec<usize> = shape
                                .iter()
                                .map(|&d| if d > 0 { d as usize } else { 1 })
                                .collect();
                            dims[0] = 0;
                            (*ty, dims)
                        }
                        _ => (TensorElementType::Float32, vec![0, 512]),
                    };
                    match elem_ty {
                        TensorElementType::Float32 => {
                            let arr = ndarray::ArrayD::<f32>::zeros(ndarray::IxDyn(&shape_vec));
                            let t = Tensor::from_array(arr)
                                .map_err(|e| format!("Failed creating {name} tensor: {e}"))?;
                            pairs.push((name.to_string(), t.into()));
                        }
                        TensorElementType::Int64 => {
                            let arr = ndarray::ArrayD::<i64>::zeros(ndarray::IxDyn(&shape_vec));
                            let t = Tensor::from_array(arr)
                                .map_err(|e| format!("Failed creating {name} tensor: {e}"))?;
                            pairs.push((name.to_string(), t.into()));
                        }
                        other => {
                            return Err(format!(
                                "Unsupported auxiliary ONNX input '{name}' element type: {other:?}"
                            ));
                        }
                    }
                }
            }
        }

        if pairs.is_empty() {
            let ids_t = Tensor::from_array(input_ids.clone())
                .map_err(|e| format!("Failed creating input_ids tensor: {e}"))?;
            let mask_t = Tensor::from_array(attention_mask.clone())
                .map_err(|e| format!("Failed creating attention_mask tensor: {e}"))?;
            pairs.push(("input_ids".to_string(), ids_t.into()));
            pairs.push(("attention_mask".to_string(), mask_t.into()));
        }

        Ok(pairs)
    }

    fn run_micro_batch(
        &mut self,
        input_ids: &Array2<i64>,
        attention_mask: &Array2<i64>,
        dimensions: usize,
    ) -> Result<Vec<Vec<f32>>, String> {
        let rows = input_ids.nrows();

        let first_attempt: Result<Vec<Vec<f32>>, String> = {
            let inputs = Self::build_session_inputs(&self.session, input_ids, attention_mask)?;
            match self.session.run(inputs) {
                Ok(out) => Self::extract_batch_outputs(&out, attention_mask, rows, dimensions),
                Err(err) => Err(format!("ONNX Runtime inference failed: {err}")),
            }
        };

        match first_attempt {
            Ok(vecs) => Ok(vecs),
            Err(dml_err) if self.active_provider != "cpu" => {
                // DirectML can reject dynamic sequence shapes or quantized ops on first inference.
                // Re-initialize once on CPU EP so inference completes reliably without falling back
                // to synthetic hash vectors.
                eprintln!(
                    "[vibetrace] {} execution failed ({dml_err}); switching EmbeddingGemma 2 session to CPU EP.",
                    self.active_provider
                );
                self.session = build_cpu_session(&self.model_path)?;
                self.active_provider = "cpu".to_string();
                let retry_inputs =
                    Self::build_session_inputs(&self.session, input_ids, attention_mask)?;
                let outputs = self
                    .session
                    .run(retry_inputs)
                    .map_err(|e| format!("ONNX Runtime inference failed on CPU EP: {e}"))?;
                Self::extract_batch_outputs(&outputs, attention_mask, rows, dimensions)
            }
            Err(err) => Err(err),
        }
    }

    fn embed_batch(
        &mut self,
        texts: &[String],
        dimensions: usize,
    ) -> Result<Vec<Vec<f32>>, String> {
        let refs: Vec<&str> = texts.iter().map(String::as_str).collect();
        let encodings = self
            .tokenizer
            .encode_batch(refs, true)
            .map_err(|e| format!("Tokenization failed: {e}"))?;

        let mut order: Vec<usize> = (0..texts.len()).collect();
        order.sort_by_key(|&i| encodings[i].get_ids().len().min(MAX_SEQ_LEN));

        let mut results = vec![Vec::new(); texts.len()];
        for chunk in order.chunks(MICRO_BATCH_SIZE) {
            let bsz = chunk.len();
            let max_len = chunk
                .iter()
                .map(|&i| encodings[i].get_ids().len().min(MAX_SEQ_LEN))
                .max()
                .unwrap_or(1)
                .max(1);

            let mut input_ids = Array2::<i64>::from_elem((bsz, max_len), self.pad_id);
            let mut attention_mask = Array2::<i64>::zeros((bsz, max_len));

            for (row_idx, &orig_idx) in chunk.iter().enumerate() {
                let enc = &encodings[orig_idx];
                let ids = enc.get_ids();
                let masks = enc.get_attention_mask();
                let seq = ids.len().min(MAX_SEQ_LEN);
                for col in 0..seq {
                    input_ids[[row_idx, col]] = i64::from(ids[col]);
                    let m = masks.get(col).copied().unwrap_or(1);
                    attention_mask[[row_idx, col]] = i64::from(m);
                }
            }

            let batch_vecs = self.run_micro_batch(&input_ids, &attention_mask, dimensions)?;
            for (&orig_idx, vec) in chunk.iter().zip(batch_vecs) {
                results[orig_idx] = vec;
            }
        }

        Ok(results)
    }
}

fn l2_normalize_in_place(vec: &mut [f32]) {
    let norm_sq: f32 = vec.iter().map(|&v| v * v).sum();
    if norm_sq > 1e-12 {
        let inv = norm_sq.sqrt().recip();
        for v in vec.iter_mut() {
            *v *= inv;
        }
    }
}

/// Embed a batch of texts using Google EmbeddingGemma 2 Q8 (`onnx-community/embeddinggemma-2-ONNX`).
pub fn embed_texts_parallel(
    texts: &[String],
    dimensions: usize,
    device: &str,
    model_dir: Option<&Path>,
) -> Result<Vec<Vec<f32>>, String> {
    let dev_key = normalize_device_request(device)?.to_string();
    if texts.is_empty() || dimensions == 0 {
        return Ok(Vec::new());
    }

    let resolved_dir = resolve_model_dir(model_dir);
    let canonical_dir = fs::canonicalize(&resolved_dir).unwrap_or_else(|_| resolved_dir.clone());
    let cache_key = (canonical_dir.clone(), dev_key.clone());

    let mut guard = engine_cache()
        .lock()
        .map_err(|_| "EmbeddingGemma 2 engine lock poisoned".to_string())?;

    if !guard.contains_key(&cache_key) {
        let engine = EmbeddingGemma2Engine::load(resolved_dir, &dev_key)?;
        guard.insert(cache_key.clone(), engine);
    }

    let engine = guard
        .get_mut(&cache_key)
        .ok_or_else(|| "EmbeddingGemma 2 engine missing from cache".to_string())?;
    if let Ok(mut prov_map) = last_active_provider_map().lock() {
        prov_map.insert(canonical_dir, engine.active_provider.clone());
    }
    engine.embed_batch(texts, dimensions)
}

/// Report EmbeddingGemma 2 cache status and active ONNX Runtime execution provider.
pub fn embedding_runtime_info(model_dir: Option<&Path>) -> HashMap<String, String> {
    let resolved_dir = resolve_model_dir(model_dir);
    let canonical_dir = fs::canonicalize(&resolved_dir).unwrap_or_else(|_| resolved_dir.clone());
    let files_present = missing_model_files(&resolved_dir).is_empty();

    let mut active_provider = "uninitialized".to_string();
    if let Ok(prov_map) = last_active_provider_map().lock() {
        if let Some(prov) = prov_map
            .get(&canonical_dir)
            .or_else(|| prov_map.get(&resolved_dir))
        {
            active_provider = prov.clone();
        }
    }
    if active_provider == "uninitialized" {
        if let Ok(guard) = engine_cache().lock() {
            for engine in guard.values() {
                let eng_canon = fs::canonicalize(&engine.cache_dir)
                    .unwrap_or_else(|_| engine.cache_dir.clone());
                if eng_canon == canonical_dir || engine.cache_dir == resolved_dir {
                    active_provider = engine.active_provider.clone();
                    break;
                }
            }
        }
    }

    let compiled_providers = if cfg!(target_os = "windows") {
        "directml,cpu"
    } else {
        "cpu"
    };

    let mut info = HashMap::new();
    info.insert("model".to_string(), MODEL_REPO_ID.to_string());
    info.insert(
        "cache_dir".to_string(),
        resolved_dir.to_string_lossy().into_owned(),
    );
    info.insert(
        "files_present".to_string(),
        if files_present { "true" } else { "false" }.to_string(),
    );
    info.insert("active_provider".to_string(), active_provider);
    info.insert(
        "compiled_providers".to_string(),
        compiled_providers.to_string(),
    );
    info
}

/// Compute dot-product cosine similarity in parallel against packed little-endian `f32` blobs.
pub fn search_vectors_parallel(
    query: &[f32],
    candidates: &[(String, Vec<u8>)],
    dimensions: usize,
    limit: usize,
) -> Vec<(String, f32)> {
    if limit == 0 || query.is_empty() || dimensions == 0 || query.len() != dimensions {
        return Vec::new();
    }
    let min_bytes = dimensions * 4;
    let mut scored: Vec<(String, f32)> = candidates
        .par_iter()
        .filter_map(|(id, blob)| {
            if blob.len() < min_bytes {
                return None;
            }
            let mut dot = 0.0f32;
            for (q_val, chunk) in query.iter().zip(blob[..min_bytes].chunks_exact(4)) {
                let c_val = f32::from_le_bytes([chunk[0], chunk[1], chunk[2], chunk[3]]);
                dot += q_val * c_val;
            }
            Some((id.clone(), dot))
        })
        .collect();

    scored.sort_by(|a, b| b.1.total_cmp(&a.1));
    scored.truncate(limit);
    scored
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    static ENV_LOCK: Mutex<()> = Mutex::new(());

    struct EnvVarGuard {
        key: &'static str,
        prev: Option<std::ffi::OsString>,
    }

    impl EnvVarGuard {
        fn set(key: &'static str, val: Option<&std::ffi::OsStr>) -> Self {
            let prev = std::env::var_os(key);
            match val {
                Some(v) => std::env::set_var(key, v),
                None => std::env::remove_var(key),
            }
            Self { key, prev }
        }
    }

    impl Drop for EnvVarGuard {
        fn drop(&mut self) {
            match &self.prev {
                Some(v) => std::env::set_var(self.key, v),
                None => std::env::remove_var(self.key),
            }
        }
    }

    #[test]
    fn test_search_vectors_parallel_ranking() {
        let to_bytes = |v: &[f32]| -> Vec<u8> { v.iter().flat_map(|f| f.to_le_bytes()).collect() };
        let v1 = vec![1.0f32, 0.0, 0.0, 0.0];
        let v2 = vec![0.0f32, 1.0, 0.0, 0.0];
        let candidates = vec![
            ("n1".to_string(), to_bytes(&v1)),
            ("n2".to_string(), to_bytes(&v2)),
        ];
        let hits = search_vectors_parallel(&[0.99, 0.01, 0.0, 0.0], &candidates, 4, 2);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].0, "n1");
        assert!(hits[0].1 > hits[1].1);
    }

    #[test]
    fn test_offline_missing_model_returns_explicit_urls_error() {
        let _lock = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let _offline = EnvVarGuard::set("VIBETRACE_OFFLINE", Some(std::ffi::OsStr::new("1")));
        let tmp = TempDir::new().expect("temp dir");
        let res = embed_texts_parallel(&["hello world".to_string()], 256, "cpu", Some(tmp.path()));

        let err = res.expect_err("expected explicit error without model files");
        assert!(err.contains(URL_TOKENIZER));
        assert!(err.contains(URL_ONNX_MODEL));
        assert!(err.contains(URL_ONNX_DATA));
        assert!(err.contains(tmp.path().to_string_lossy().as_ref()));
    }

    #[test]
    fn test_normalize_device_request_rejects_auto() {
        assert_eq!(normalize_device_request("gpu").unwrap(), "gpu");
        assert_eq!(normalize_device_request("").unwrap(), "gpu");
        assert_eq!(normalize_device_request("directml").unwrap(), "gpu");
        assert_eq!(normalize_device_request("cpu").unwrap(), "cpu");

        let err = normalize_device_request("auto").expect_err("auto must be rejected");
        assert!(err.contains("Invalid compute device"));
        assert!(normalize_device_request("tpu").is_err());
    }

    #[test]
    fn test_l2_normalize_in_place_unit_and_zero_vectors() {
        let mut unit = vec![3.0f32, 4.0f32];
        l2_normalize_in_place(&mut unit);
        assert!((unit[0] - 0.6).abs() < 1e-6);
        assert!((unit[1] - 0.8).abs() < 1e-6);
        let norm = (unit[0] * unit[0] + unit[1] * unit[1]).sqrt();
        assert!((norm - 1.0).abs() < 1e-6);

        let mut zero = vec![0.0f32, 0.0f32, 0.0f32];
        l2_normalize_in_place(&mut zero);
        assert!(zero.iter().all(|v| v.is_finite() && *v == 0.0));
    }

    #[test]
    fn test_search_vectors_parallel_edge_cases() {
        let to_bytes = |v: &[f32]| -> Vec<u8> { v.iter().flat_map(|f| f.to_le_bytes()).collect() };
        let candidates = vec![
            ("short".to_string(), to_bytes(&[1.0f32, 0.0])),
            (
                "matryoshka_pos".to_string(),
                to_bytes(&[0.5f32, 0.5, 99.0, 99.0]),
            ),
            ("neg".to_string(), to_bytes(&[-1.0f32, 0.0, 0.0])),
            ("best".to_string(), to_bytes(&[1.0f32, 0.0, 0.0])),
        ];

        assert!(search_vectors_parallel(&[1.0, 0.0, 0.0], &candidates, 3, 0).is_empty());
        assert!(search_vectors_parallel(&[], &candidates, 0, 5).is_empty());
        assert!(search_vectors_parallel(&[1.0, 0.0], &candidates, 3, 5).is_empty());

        let hits = search_vectors_parallel(&[1.0, 0.0, 0.0], &candidates, 3, 2);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].0, "best");
        assert!((hits[0].1 - 1.0).abs() < 1e-6);
        assert_eq!(hits[1].0, "matryoshka_pos");
        assert!((hits[1].1 - 0.5).abs() < 1e-6);

        let all_hits = search_vectors_parallel(&[1.0, 0.0, 0.0], &candidates, 3, 10);
        assert_eq!(all_hits.len(), 3);
        assert_eq!(all_hits[2].0, "neg");
        assert!(all_hits[2].1 < 0.0);
    }

    #[test]
    fn test_resolve_model_dir_and_runtime_info_cache_detection() {
        let _lock = ENV_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        let tmp = TempDir::new().expect("temp dir");
        assert_eq!(resolve_model_dir(Some(tmp.path())), tmp.path());

        {
            let env_path = tmp.path().join("custom_models");
            let _model_env = EnvVarGuard::set("VIBETRACE_MODEL_DIR", Some(env_path.as_os_str()));
            assert_eq!(resolve_model_dir(Some(Path::new(""))), env_path);
        }

        let info_empty = embedding_runtime_info(Some(tmp.path()));
        assert_eq!(
            info_empty.get("files_present").map(String::as_str),
            Some("false")
        );

        fs::write(tmp.path().join(TOKENIZER_FILE), b"").unwrap();
        fs::write(tmp.path().join(ONNX_MODEL_FILE), b"onnx").unwrap();
        fs::write(tmp.path().join(ONNX_DATA_FILE), b"weights").unwrap();
        let info_zero_byte = embedding_runtime_info(Some(tmp.path()));
        assert_eq!(
            info_zero_byte.get("files_present").map(String::as_str),
            Some("false")
        );

        fs::write(tmp.path().join(TOKENIZER_FILE), b"{}").unwrap();
        assert!(missing_model_files(tmp.path()).is_empty());
        let info_ready = embedding_runtime_info(Some(tmp.path()));
        assert_eq!(
            info_ready.get("files_present").map(String::as_str),
            Some("true")
        );
        assert_eq!(
            info_ready.get("model").map(String::as_str),
            Some(MODEL_REPO_ID)
        );
    }

    #[test]
    fn test_embed_texts_parallel_short_circuits_and_device_validation() {
        let tmp = TempDir::new().expect("temp dir");
        assert!(embed_texts_parallel(&[], 256, "cpu", Some(tmp.path()))
            .unwrap()
            .is_empty());
        assert!(
            embed_texts_parallel(&["hi".to_string()], 0, "cpu", Some(tmp.path()))
                .unwrap()
                .is_empty()
        );
        assert!(embed_texts_parallel(&[], 256, "auto", Some(tmp.path())).is_err());
    }

    #[test]
    fn test_device_aliases_and_gpu_unavailable_error_message() {
        assert_eq!(normalize_device_request("  GPU  ").unwrap(), "gpu");
        assert_eq!(normalize_device_request("CUDA").unwrap(), "gpu");
        assert_eq!(normalize_device_request("DML").unwrap(), "gpu");
        assert_eq!(normalize_device_request("  CPU ").unwrap(), "cpu");

        let msg = gpu_unavailable_error("test detail");
        assert!(msg.contains("test detail"));
        assert!(msg.contains("--device cpu"));
        assert!(msg.contains(".vibetrace.toml"));
    }
}
