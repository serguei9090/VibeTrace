//! Parallel filesystem walker and incremental SHA-256 hasher powered by `ignore` and `rayon`.

use crate::models::{RustFileDelta, RustWorkspaceScanResult};
use globset::{Glob, GlobSet, GlobSetBuilder};
use ignore::WalkBuilder;
use rayon::prelude::*;
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};

const SKIP_DIRS: &[&str] = &[
    ".git",
    ".venv",
    ".vibetrace",
    "node_modules",
    "__pycache__",
    ".ruff_cache",
    "dist",
    "build",
    "target",
    "vibetrace_context",
    "vibegraph_context",
];

const SUPPORTED_EXTENSIONS: &[&str] = &[
    "py", "js", "jsx", "ts", "tsx", "go", "rs", "java", "c", "h", "cpp", "cc", "cxx", "hpp", "cs",
    "rb", "php",
];

/// Returns true if the file has a supported source code extension for VibeTrace AST indexing.
pub fn is_supported_source_file(rel_path: &str) -> bool {
    let lower = rel_path.to_ascii_lowercase();
    if let Some((_, ext)) = lower.rsplit_once('.') {
        SUPPORTED_EXTENSIONS.contains(&ext)
    } else {
        false
    }
}

/// Checks if any directory segment in `rel_path` matches `SKIP_DIRS` or starts with `.`.
pub fn has_skipped_dir_component(rel_path: &str, is_dir: bool) -> bool {
    let clean = rel_path.replace('\\', "/");
    let trimmed = clean.trim_matches('/');
    if trimmed.is_empty() {
        return false;
    }
    let parts: Vec<&str> = trimmed.split('/').collect();
    let dir_parts = if is_dir {
        &parts[..]
    } else if parts.len() > 1 {
        &parts[..parts.len() - 1]
    } else {
        &[]
    };
    dir_parts
        .iter()
        .any(|p| SKIP_DIRS.contains(p) || p.starts_with('.'))
}

fn build_exclude_globset(extra_excludes: &[String]) -> Option<GlobSet> {
    if extra_excludes.is_empty() {
        return None;
    }
    let mut builder = GlobSetBuilder::new();
    let mut added = 0;
    for pat in extra_excludes {
        let trimmed = pat.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Ok(g) = Glob::new(trimmed) {
            builder.add(g);
            added += 1;
        }
        let clean = trimmed.trim_matches('/');
        if !clean.contains('*') {
            if let Ok(g2) = Glob::new(&format!("**/{clean}/**")) {
                builder.add(g2);
                added += 1;
            }
        }
    }
    if added == 0 {
        None
    } else {
        builder.build().ok()
    }
}

/// Normalizes a path relative to `project_root` using forward slashes.
pub fn normalize_rel_path(full_path: &Path, project_root: &Path) -> String {
    match full_path.strip_prefix(project_root) {
        Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
        Err(_) => full_path.to_string_lossy().replace('\\', "/"),
    }
}

/// Scans `target_path` using `ignore::WalkBuilder`, filters ignored directories and unsupported
/// extensions, and computes SHA-256 hashes in parallel across all CPU cores using `rayon`.
pub fn scan_and_hash_workspace(
    target_path: &Path,
    project_root: &Path,
    known_hashes: &HashMap<String, String>,
    extra_excludes: &[String],
    force: bool,
) -> RustWorkspaceScanResult {
    let target_rel = normalize_rel_path(target_path, project_root);
    let glob_excludes = build_exclude_globset(extra_excludes);

    let mut candidate_files: Vec<(PathBuf, String)> = Vec::new();

    if target_path.is_file() {
        let rel = normalize_rel_path(target_path, project_root);
        if !has_skipped_dir_component(&rel, false) && is_supported_source_file(&rel) {
            candidate_files.push((target_path.to_path_buf(), rel));
        }
    } else if target_path.is_dir() {
        let mut builder = WalkBuilder::new(target_path);
        builder
            .hidden(true)
            .git_ignore(true)
            .git_global(false)
            .git_exclude(true)
            .require_git(false)
            .parents(true);

        let root_clone = project_root.to_path_buf();
        builder.filter_entry(move |entry| {
            let p = entry.path();
            if p == root_clone {
                return true;
            }
            let is_dir = entry.file_type().is_some_and(|ft| ft.is_dir());
            if let Ok(rel) = p.strip_prefix(&root_clone) {
                let rel_str = rel.to_string_lossy().replace('\\', "/");
                if has_skipped_dir_component(&rel_str, is_dir) {
                    return false;
                }
            }
            true
        });

        for result in builder.build().flatten() {
            if !result.file_type().is_some_and(|ft| ft.is_file()) {
                continue;
            }
            let full_p = result.path().to_path_buf();
            let rel = normalize_rel_path(&full_p, project_root);
            if has_skipped_dir_component(&rel, false) {
                continue;
            }
            if let Some(ref gs) = glob_excludes {
                if gs.is_match(&rel) {
                    continue;
                }
            }
            if is_supported_source_file(&rel) {
                candidate_files.push((full_p, rel));
            }
        }
    }

    let seen_set: HashSet<String> = candidate_files.iter().map(|(_, r)| r.clone()).collect();
    let total_seen = seen_set.len();

    // Parallel SHA-256 hashing and diff classification via Rayon
    let processed: Vec<Result<RustFileDelta, String>> = candidate_files
        .into_par_iter()
        .filter_map(|(full_p, rel)| {
            let bytes = fs::read(&full_p).ok()?;
            let mut hasher = Sha256::new();
            hasher.update(&bytes);
            let hash_hex = hex::encode(hasher.finalize());

            if !force {
                if let Some(existing_hash) = known_hashes.get(&rel) {
                    if existing_hash == &hash_hex {
                        return Some(Err(rel));
                    }
                }
            }

            Some(Ok(RustFileDelta {
                rel_path: rel,
                content_hash: hash_hex,
                source_bytes: bytes,
            }))
        })
        .collect();

    let mut dirty_files = Vec::new();
    let mut unchanged_files = Vec::new();
    for item in processed {
        match item {
            Ok(delta) => dirty_files.push(delta),
            Err(unchanged_rel) => unchanged_files.push(unchanged_rel),
        }
    }

    // Reconcile deleted files within target scope
    let mut deleted_files = Vec::new();
    if target_path.is_dir() {
        let prefix = if target_rel.is_empty() || target_rel == "." {
            String::new()
        } else {
            format!("{target_rel}/")
        };
        for indexed_file in known_hashes.keys() {
            if !prefix.is_empty()
                && indexed_file != &target_rel
                && !indexed_file.starts_with(&prefix)
            {
                continue;
            }
            if !seen_set.contains(indexed_file) {
                deleted_files.push(indexed_file.clone());
            }
        }
    }

    dirty_files.sort_by(|a, b| a.rel_path.cmp(&b.rel_path));
    unchanged_files.sort();
    deleted_files.sort();

    RustWorkspaceScanResult {
        dirty_files,
        unchanged_files,
        deleted_files,
        total_seen,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    #[test]
    fn test_gitignore_and_skip_dirs() {
        let tmp = TempDir::new().expect("tempdir");
        let root = tmp.path();

        fs::write(root.join(".gitignore"), "ignored_dir/\n*.gen.py\n").unwrap();
        fs::create_dir_all(root.join("src")).unwrap();
        fs::create_dir_all(root.join("node_modules/pkg")).unwrap();
        fs::create_dir_all(root.join(".venv/lib")).unwrap();
        fs::create_dir_all(root.join("ignored_dir")).unwrap();

        fs::write(root.join("src/app.py"), b"def main(): pass\n").unwrap();
        fs::write(root.join("src/schema.gen.py"), b"x = 1\n").unwrap();
        fs::write(
            root.join("node_modules/pkg/index.js"),
            b"export default 1;\n",
        )
        .unwrap();
        fs::write(root.join(".venv/lib/site.py"), b"pass\n").unwrap();
        fs::write(root.join("ignored_dir/secret.py"), b"pass\n").unwrap();

        let known = HashMap::new();
        let res = scan_and_hash_workspace(root, root, &known, &[], false);

        assert_eq!(res.total_seen, 1);
        assert_eq!(res.dirty_files.len(), 1);
        assert_eq!(res.dirty_files[0].rel_path, "src/app.py");
    }

    #[test]
    fn test_sha256_incremental_diff() {
        let tmp = TempDir::new().expect("tempdir");
        let root = tmp.path();

        fs::write(root.join("unchanged.py"), b"def a(): return 1\n").unwrap();
        fs::write(root.join("modified.py"), b"def b(): return 2\n").unwrap();

        let initial = scan_and_hash_workspace(root, root, &HashMap::new(), &[], false);
        assert_eq!(initial.dirty_files.len(), 2);

        let mut known = HashMap::new();
        for d in &initial.dirty_files {
            known.insert(d.rel_path.clone(), d.content_hash.clone());
        }
        known.insert("deleted_old.py".to_string(), "deadbeef".to_string());

        // Modify only modified.py and add new.py
        fs::write(root.join("modified.py"), b"def b(): return 999\n").unwrap();
        fs::write(root.join("added.py"), b"def c(): return 3\n").unwrap();

        let second = scan_and_hash_workspace(root, root, &known, &[], false);
        let dirty_paths: Vec<&str> = second
            .dirty_files
            .iter()
            .map(|d| d.rel_path.as_str())
            .collect();
        assert_eq!(dirty_paths, vec!["added.py", "modified.py"]);
        assert_eq!(second.unchanged_files, vec!["unchanged.py"]);
        assert_eq!(second.deleted_files, vec!["deleted_old.py"]);
    }
}
