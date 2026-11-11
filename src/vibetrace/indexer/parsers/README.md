# Parsers

## Overview

Tree-sitter language extractors for the VibeTrace indexer. Each parser turns one
source file into `Node` and `Edge` graph records: a module node for the file,
definition nodes for functions, classes, methods, and types, call/import/reference
edges resolved against imported symbols, and synthetic endpoint nodes for HTTP
route decorators.

`ParserFactory` selects a parser by file extension. All parsers share
`LanguageParser` base behavior, `ParserContext` state, and the builtin-name sets
used to suppress unresolvable language builtins.

## Interface

- [src/vibetrace/indexer/parsers/__init__.py:ParserFactory] - Extension-to-parser registry; `get_parser` and `get_supported_extensions`.
- [src/vibetrace/indexer/parsers/base.py:LanguageParser] - Abstract base parser; `extract` is the required entry point, plus shared call resolution and node/edge construction.
- [src/vibetrace/indexer/parsers/base.py:ParserContext] - Per-file extraction state: nodes, edges, scope stack, imported symbols, local types, defined symbols, test fixtures, and unresolved target symbols.
- [src/vibetrace/indexer/parsers/base.py:get_language] / [src/vibetrace/indexer/parsers/base.py:get_parser] - Cached tree-sitter `Language` and `Parser` construction.
- [src/vibetrace/indexer/parsers/python_parser.py:PythonParser] - Python AST extraction with decorators, docstrings, type aliases, pytest fixtures, and match-case patterns.
- [src/vibetrace/indexer/parsers/python_helpers.py:extract_python_imports] / [src/vibetrace/indexer/parsers/python_helpers.py:extract_pattern_references] / [src/vibetrace/indexer/parsers/python_helpers.py:resolve_deferred_fixtures] / [src/vibetrace/indexer/parsers/python_helpers.py:link_all_exports] - Python import resolution, pattern references, deferred fixture links, and `__all__` re-export linking.
- [src/vibetrace/indexer/parsers/js_parser.py:JavaScriptParser] - JavaScript and JSX extraction with `require` and ES module import/export handling.
- [src/vibetrace/indexer/parsers/ts_parser.py:TypeScriptParser] - TypeScript and TSX parser built on `JavaScriptParser`.
- [src/vibetrace/indexer/parsers/go_parser.py:GoParser] - Go extraction with type-directed call resolution and struct field tracking.
- [src/vibetrace/indexer/parsers/rust_parser.py:RustParser] - Rust extraction with module, struct, trait, and impl scope handling.
- [src/vibetrace/indexer/parsers/generic_parser.py:GenericParser] - Shared parser for Java, C, C++, C#, Ruby, and PHP.
- [src/vibetrace/indexer/parsers/route_extractor.py:extract_python_routes] - FastAPI and Flask decorator routes to endpoint nodes.
- [src/vibetrace/indexer/parsers/route_extractor.py:extract_python_router_prefixes] - `APIRouter` and `Blueprint` prefix extraction.
- [src/vibetrace/indexer/parsers/route_extractor.py:extract_js_endpoint_route] - JS/TS client and backend route extraction.
- [src/vibetrace/indexer/parsers/route_extractor.py:extract_enterprise_class_prefix] / [src/vibetrace/indexer/parsers/route_extractor.py:extract_enterprise_routes] - Spring and ASP.NET class prefix and route extraction.
- [src/vibetrace/indexer/parsers/route_extractor.py:FASTAPI_ROUTE_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:FLASK_ROUTE_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:FLASK_METHODS_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:API_ROUTER_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:BLUEPRINT_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:API_ROUTE_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:SPRING_MAPPING_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:SPRING_REQUEST_MAPPING_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:CSHARP_HTTP_RE] / [src/vibetrace/indexer/parsers/route_extractor.py:CSHARP_ROUTE_RE] - Framework route patterns matched by the route extractors.
- [src/vibetrace/indexer/parsers/builtins.py:PYTHON_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:JS_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:JS_GLOBALS] / [src/vibetrace/indexer/parsers/builtins.py:GO_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:RUST_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:JAVA_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:CPP_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:CSHARP_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:RUBY_BUILTINS] / [src/vibetrace/indexer/parsers/builtins.py:PHP_BUILTINS] - Per-language builtin and global name sets.

## Invariants

- `ParserFactory.get_parser` returns `None` for extensions outside `SUPPORTED_EXTENSIONS`; callers treat that as "unsupported language" and skip the file [src/vibetrace/indexer/parsers/__init__.py:ParserFactory.get_parser].
- Blank or whitespace-only source yields no nodes and no edges [src/vibetrace/indexer/parsers/python_parser.py:PythonParser.extract], [src/vibetrace/indexer/parsers/generic_parser.py:GenericParser.extract], [src/vibetrace/indexer/parsers/rust_parser.py:RustParser.extract].
- Every parsed file emits exactly one module node whose ID is derived from the `module` kind hash [src/vibetrace/indexer/parsers/base.py:LanguageParser._get_id].
- Node IDs are deterministic: MD5 of `file_path::qualified_name`, with module nodes keyed by `module::qualified_name` [src/vibetrace/indexer/parsers/base.py:LanguageParser._get_id].
- A bare call to a language builtin produces no edge; builtins are excluded unless the name is also defined or imported in the file [src/vibetrace/indexer/parsers/base.py:LanguageParser._is_builtin].
- Import edges are `confidence = "exact"` when the module resolves to a project file and `confidence = "inferred"` when it falls back to `external` [src/vibetrace/indexer/parsers/base.py:LanguageParser._register_imported_module].
- Route decorators yield synthetic nodes with kind `endpoint` plus a `routes` edge to the handler node [src/vibetrace/indexer/parsers/route_extractor.py:extract_python_routes], [src/vibetrace/indexer/parsers/route_extractor.py:extract_js_endpoint_route].
- Parsers mutate `last_imported_symbols` and `last_target_symbols` as per-file state; callers reset both before each extraction so results belong to one file only [src/vibetrace/indexer/parsers/base.py:LanguageParser.__init__], [src/vibetrace/indexer/main.py:index_file].
- Top-level symbol names defined in the file are collected before traversal so builtin filtering cannot discard a locally defined symbol [src/vibetrace/indexer/parsers/python_parser.py:PythonParser.extract], [src/vibetrace/indexer/parsers/rust_parser.py:RustParser.extract].

## Side Effects

- Reads sibling `.go` files from the project root to detect struct declarations for type-directed edges [src/vibetrace/indexer/parsers/go_parser.py:GoParser.extract].
- Reads project files to build a cached per-package symbol index for Go resolution [src/vibetrace/indexer/parsers/base.py:LanguageParser._find_go_symbol_file].
- Mutates parser instance state `last_imported_symbols` and `last_target_symbols` during extraction [src/vibetrace/indexer/parsers/base.py:LanguageParser._get_id], [src/vibetrace/indexer/parsers/base.py:LanguageParser._register_imported_module].
- Caches tree-sitter `Language` instances process-wide in `_LANGUAGES_CACHE` [src/vibetrace/indexer/parsers/base.py:get_language].
- Creates local directory and file nodes describing the parsed file; persistence is performed by the caller [src/vibetrace/indexer/parsers/base.py:LanguageParser._create_node].

## Verification

- Run: `uv run pytest`
