//! Native GIL-free parallel vector embedding and cosine similarity search (`rayon`).

use rayon::prelude::*;

#[inline]
fn fnv1a_splitmix(bytes: &[u8], seed: u64) -> u64 {
    let mut h: u64 = 0xcbf2_9ce4_8422_2325 ^ seed;
    for &b in bytes {
        h ^= u64::from(b);
        h = h.wrapping_mul(0x0100_0000_01b3);
    }
    h ^= h >> 30;
    h = h.wrapping_mul(0xbf58_476d_1ce4_e5b9);
    h ^= h >> 27;
    h = h.wrapping_mul(0x94d0_49bb_1331_11eb);
    h ^ (h >> 31)
}

fn push_feature(acc: &mut [f32], token: &[u8], weight: f32) {
    let dims = acc.len();
    if dims == 0 || token.is_empty() {
        return;
    }
    let h = fnv1a_splitmix(token, 0x9e37_79b9_7f4a_7c15);
    let bucket = (h as usize) % dims;
    let sign = if ((h >> 32) & 1) == 0 {
        1.0f32
    } else {
        -1.0f32
    };
    acc[bucket] += sign * weight;
}

/// Extract identifier tokens, camelCase/snake_case sub-tokens, and 3-char trigrams
/// into a deterministic L2-normalized feature-hashing vector.
pub fn embed_single(text: &str, dimensions: usize) -> Vec<f32> {
    if dimensions == 0 {
        return Vec::new();
    }
    let mut vec = vec![0.0f32; dimensions];
    let lower = text.to_lowercase();

    for raw_tok in lower.split(|c: char| !c.is_alphanumeric() && c != '_') {
        if raw_tok.is_empty() {
            continue;
        }
        push_feature(&mut vec, raw_tok.as_bytes(), 1.5);
        for sub in raw_tok.split('_').filter(|s| !s.is_empty()) {
            push_feature(&mut vec, sub.as_bytes(), 1.0);
            let sb = sub.as_bytes();
            if sb.len() >= 3 {
                for w in sb.windows(3) {
                    push_feature(&mut vec, w, 0.5);
                }
            }
        }
    }

    // Also hash camelCase splits from original text
    let mut cur = String::new();
    for ch in text.chars() {
        if ch.is_alphanumeric() {
            if ch.is_uppercase() && !cur.is_empty() {
                push_feature(&mut vec, cur.to_lowercase().as_bytes(), 1.0);
                cur.clear();
            }
            cur.push(ch);
        } else if !cur.is_empty() {
            push_feature(&mut vec, cur.to_lowercase().as_bytes(), 1.0);
            cur.clear();
        }
    }
    if !cur.is_empty() {
        push_feature(&mut vec, cur.to_lowercase().as_bytes(), 1.0);
    }

    let norm_sq: f32 = vec.iter().map(|v| v * v).sum();
    if norm_sq > 1e-12 {
        let inv = norm_sq.sqrt().recip();
        for v in &mut vec {
            *v *= inv;
        }
    }
    vec
}

/// Embed a batch of texts in parallel across all CPU cores via Rayon.
pub fn embed_texts_parallel(texts: &[String], dimensions: usize) -> Vec<Vec<f32>> {
    if texts.is_empty() || dimensions == 0 {
        return Vec::new();
    }
    texts
        .par_iter()
        .map(|t| embed_single(t, dimensions))
        .collect()
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

    #[test]
    fn test_embed_normalized_and_deterministic() {
        let a = embed_texts_parallel(&["AuthService.authenticate_jwt".to_string()], 256);
        let b = embed_texts_parallel(&["AuthService.authenticate_jwt".to_string()], 256);
        assert_eq!(a.len(), 1);
        assert_eq!(a[0].len(), 256);
        assert_eq!(a[0], b[0]);
        let norm_sq: f32 = a[0].iter().map(|x| x * x).sum();
        assert!((norm_sq - 1.0).abs() < 1e-4);
    }

    #[test]
    fn test_search_vectors_parallel_ranking() {
        let vecs = embed_texts_parallel(
            &[
                "AuthService.authenticate_jwt".to_string(),
                "DatabasePool.connect_pool".to_string(),
            ],
            64,
        );
        let to_bytes = |v: &[f32]| -> Vec<u8> { v.iter().flat_map(|f| f.to_le_bytes()).collect() };
        let candidates = vec![
            ("n1".to_string(), to_bytes(&vecs[0])),
            ("n2".to_string(), to_bytes(&vecs[1])),
        ];
        let q = embed_single("authenticate jwt", 64);
        let hits = search_vectors_parallel(&q, &candidates, 64, 2);
        assert_eq!(hits.len(), 2);
        assert_eq!(hits[0].0, "n1");
        assert!(hits[0].1 >= hits[1].1);
    }
}
