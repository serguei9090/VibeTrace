//! Multi-core parallel AST extraction using Rayon and Tree-sitter in Rust.

use crate::models::{RustEdge, RustFileDelta, RustFileExtraction, RustNode};
use md5::{Digest as Md5Digest, Md5};
use rayon::prelude::*;
use std::collections::{HashMap, HashSet};
use tree_sitter::{Language, Node, Parser};

/// Compute deterministic MD5 hex ID matching Python's `LanguageParser._get_id`.
pub fn compute_node_id(
    file_path: &str,
    qualified_name: &str,
    kind: Option<&str>,
    target_symbols: &mut HashMap<String, (String, String)>,
) -> String {
    let mut hasher = Md5::new();
    if kind == Some("module") {
        hasher.update(format!("module::{qualified_name}").as_bytes());
        return hex::encode(hasher.finalize());
    }
    hasher.update(format!("{file_path}::{qualified_name}").as_bytes());
    let nid = hex::encode(hasher.finalize());
    target_symbols.insert(
        nid.clone(),
        (file_path.to_string(), qualified_name.to_string()),
    );
    nid
}

fn get_ts_language(ext: &str) -> Option<Language> {
    match ext {
        "py" => Some(tree_sitter_python::LANGUAGE.into()),
        "js" | "jsx" => Some(tree_sitter_javascript::LANGUAGE.into()),
        "ts" => Some(tree_sitter_typescript::LANGUAGE_TYPESCRIPT.into()),
        "tsx" => Some(tree_sitter_typescript::LANGUAGE_TSX.into()),
        "go" => Some(tree_sitter_go::LANGUAGE.into()),
        "rs" => Some(tree_sitter_rust::LANGUAGE.into()),
        _ => None,
    }
}

fn node_text<'a>(node: Node<'a>, source: &'a [u8]) -> &'a str {
    node.utf8_text(source).unwrap_or("")
}

fn extract_signature(node: Node, source: &[u8]) -> Option<String> {
    let raw = node_text(node, source);
    let first_line = raw.lines().next()?.trim();
    let cleaned = first_line
        .trim_end_matches('{')
        .trim_end_matches(':')
        .trim();
    if cleaned.is_empty() {
        None
    } else {
        Some(cleaned.to_string())
    }
}

fn extract_python_docstring(node: Node, source: &[u8]) -> Option<String> {
    let body = node.child_by_field_name("body")?;
    let mut cursor = body.walk();
    for child in body.children(&mut cursor) {
        if child.kind() == "expression_statement" {
            if let Some(first) = child.child(0) {
                if first.kind() == "string" {
                    let raw = node_text(first, source)
                        .trim_matches(|c| c == '"' || c == '\'')
                        .trim();
                    if !raw.is_empty() {
                        return Some(raw.to_string());
                    }
                }
            }
        } else if child.kind() != "comment" {
            break;
        }
    }
    None
}

struct ExtractionContext<'a> {
    rel_path: &'a str,
    source: &'a [u8],
    ext: &'a str,
    nodes: Vec<RustNode>,
    edges: Vec<RustEdge>,
    scope_stack: Vec<String>,
    defined_symbols: HashSet<String>,
    imported_symbols: HashMap<String, (String, String)>,
    target_symbols: HashMap<String, (String, String)>,
}

impl<'a> ExtractionContext<'a> {
    fn scoped_name(&self, name: &str) -> String {
        if self.scope_stack.is_empty() {
            name.to_string()
        } else {
            format!("{}.{name}", self.scope_stack.join("."))
        }
    }

    fn walk_ast(&mut self, node: Node<'a>, parent_id: &str) {
        let kind_str = node.kind();

        // 1. Symbol definitions across Python, TS/JS, Go, and Rust
        let is_func = matches!(
            kind_str,
            "function_definition"
                | "function_declaration"
                | "method_definition"
                | "method_declaration"
                | "function_item"
        );
        let is_class = matches!(
            kind_str,
            "class_definition"
                | "class_declaration"
                | "interface_declaration"
                | "type_alias_declaration"
                | "struct_item"
                | "enum_item"
                | "trait_item"
                | "type_spec"
        );

        if is_func || is_class {
            if let Some(name_node) = node.child_by_field_name("name") {
                let sym_name = node_text(name_node, self.source).trim().to_string();
                if !sym_name.is_empty() {
                    let qname = self.scoped_name(&sym_name);
                    let node_kind = if is_class {
                        match kind_str {
                            "interface_declaration" | "trait_item" => "interface",
                            "struct_item" => "struct",
                            "enum_item" => "enum",
                            _ => "class",
                        }
                    } else if !self.scope_stack.is_empty() {
                        "method"
                    } else {
                        "function"
                    };

                    let nid = compute_node_id(
                        self.rel_path,
                        &qname,
                        Some(node_kind),
                        &mut self.target_symbols,
                    );
                    let visibility = if sym_name.starts_with('_') && !sym_name.starts_with("__") {
                        "private"
                    } else {
                        "public"
                    };
                    let doc = if self.ext == "py" {
                        extract_python_docstring(node, self.source)
                    } else {
                        None
                    };

                    self.defined_symbols.insert(sym_name.clone());
                    self.nodes.push(RustNode {
                        id: nid.clone(),
                        name: sym_name.clone(),
                        qualified_name: qname,
                        parent_id: Some(parent_id.to_string()),
                        kind: node_kind.to_string(),
                        file_path: self.rel_path.to_string(),
                        start_line: node.start_position().row + 1,
                        end_line: node.end_position().row + 1,
                        signature: extract_signature(node, self.source),
                        docstring: doc,
                        decorators: None,
                        visibility: visibility.to_string(),
                        centrality: 0.0,
                    });

                    self.edges.push(RustEdge {
                        from_node_id: parent_id.to_string(),
                        to_node_id: nid.clone(),
                        relation_type: "defines".to_string(),
                        call_line: Some(node.start_position().row + 1),
                        confidence: "exact".to_string(),
                    });

                    self.scope_stack.push(sym_name);
                    let mut cursor = node.walk();
                    for child in node.children(&mut cursor) {
                        self.walk_ast(child, &nid);
                    }
                    self.scope_stack.pop();
                    return;
                }
            }
        }

        // 2. Call expressions
        if matches!(kind_str, "call" | "call_expression") {
            let func_field = node
                .child_by_field_name("function")
                .or_else(|| node.child(0));
            if let Some(fnode) = func_field {
                let raw_callee = node_text(fnode, self.source).trim();
                let bare_name = raw_callee
                    .rsplit_once('.')
                    .map(|(_, r)| r)
                    .or_else(|| raw_callee.rsplit_once("::").map(|(_, r)| r))
                    .unwrap_or(raw_callee)
                    .trim();

                if !bare_name.is_empty()
                    && bare_name
                        .chars()
                        .all(|c| c.is_alphanumeric() || c == '_' || c == '$')
                {
                    let target_id =
                        compute_node_id(self.rel_path, bare_name, None, &mut self.target_symbols);
                    self.edges.push(RustEdge {
                        from_node_id: parent_id.to_string(),
                        to_node_id: target_id,
                        relation_type: "calls".to_string(),
                        call_line: Some(node.start_position().row + 1),
                        confidence: "inferred".to_string(),
                    });
                }
            }
        }

        let mut cursor = node.walk();
        for child in node.children(&mut cursor) {
            self.walk_ast(child, parent_id);
        }
    }
}

/// Extract AST symbols and call edges for a single file using native Rust Tree-sitter.
pub fn extract_file_ast(delta: &RustFileDelta) -> Option<RustFileExtraction> {
    let ext = delta.rel_path.rsplit_once('.')?.1.to_ascii_lowercase();
    let language = get_ts_language(&ext)?;

    let mut parser = Parser::new();
    parser.set_language(&language).ok()?;
    let tree = parser.parse(&delta.source_bytes, None)?;

    let norm_path = delta.rel_path.replace('\\', "/");
    let file_stem = norm_path
        .rsplit_once('/')
        .map(|(_, f)| f)
        .unwrap_or(&norm_path);
    let module_name = file_stem
        .rsplit_once('.')
        .map(|(s, _)| s)
        .unwrap_or(file_stem);

    let mut target_symbols = HashMap::new();
    let module_id = compute_node_id(&norm_path, module_name, Some("module"), &mut target_symbols);

    let root_node = tree.root_node();
    let module_rust_node = RustNode {
        id: module_id.clone(),
        name: module_name.to_string(),
        qualified_name: module_name.to_string(),
        parent_id: None,
        kind: "module".to_string(),
        file_path: norm_path.clone(),
        start_line: root_node.start_position().row + 1,
        end_line: root_node.end_position().row + 1,
        signature: None,
        docstring: None,
        decorators: None,
        visibility: "public".to_string(),
        centrality: 0.0,
    };

    let mut ctx = ExtractionContext {
        rel_path: &norm_path,
        source: &delta.source_bytes,
        ext: &ext,
        nodes: vec![module_rust_node],
        edges: Vec::new(),
        scope_stack: Vec::new(),
        defined_symbols: HashSet::new(),
        imported_symbols: HashMap::new(),
        target_symbols,
    };

    ctx.walk_ast(root_node, &module_id);
    let ExtractionContext {
        nodes,
        edges,
        imported_symbols,
        target_symbols,
        ..
    } = ctx;

    Some(RustFileExtraction {
        rel_path: norm_path,
        content_hash: delta.content_hash.clone(),
        nodes,
        edges,
        imported_symbols,
        target_symbols,
    })
}

/// Extract AST symbols in parallel across a batch of dirty files using Rayon.
pub fn extract_batch_parallel(deltas: &[RustFileDelta]) -> Vec<RustFileExtraction> {
    deltas.par_iter().filter_map(extract_file_ast).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parallel_ast_extraction() {
        let py_delta = RustFileDelta {
            rel_path: "src/service.py".to_string(),
            content_hash: "abc1234".to_string(),
            source_bytes: b"class AuthService:\n    \"\"\"Auth doc.\"\"\"\n    def login(self, user: str) -> bool:\n        return verify_token(user)\n".to_vec(),
        };
        let ts_delta = RustFileDelta {
            rel_path: "src/client.ts".to_string(),
            content_hash: "def5678".to_string(),
            source_bytes: b"export function fetchUser(id: string) {\n    return apiCall(id);\n}\n"
                .to_vec(),
        };

        let results = extract_batch_parallel(&[py_delta, ts_delta]);
        assert_eq!(results.len(), 2);

        let py_res = results
            .iter()
            .find(|r| r.rel_path == "src/service.py")
            .unwrap();
        let names: Vec<&str> = py_res
            .nodes
            .iter()
            .map(|n| n.qualified_name.as_str())
            .collect();
        assert!(names.contains(&"service"));
        assert!(names.contains(&"AuthService"));
        assert!(names.contains(&"AuthService.login"));

        let call_edges: Vec<&RustEdge> = py_res
            .edges
            .iter()
            .filter(|e| e.relation_type == "calls")
            .collect();
        assert_eq!(call_edges.len(), 1);
    }
}
