# Code Quality and Maintainability Analysis

## Overview
Static analysis of source quality for the VibeTrace toolchain. The package
produces `QualityFinding` records normalized to SonarSource RSPEC identifiers
and covers cognitive complexity, clean code smells, duplicated literals, CSS
accessibility, Dockerfile maintainability, Kubernetes manifest hygiene, and
declarative Semgrep-style rules. `QualityService` coordinates the analyzers,
external linter sub-engines, inline suppression handling, and the
`.vibetrace/quality.toml` baseline.

Rule definitions live in `QUALITY_RULES_CATALOG`, a catalog of `QualityRuleSpec`
entries carrying severity, category, CWE and OWASP mappings, explanations, and
compliant and non-compliant examples. Vendor rule IDs are resolved to canonical
IDs through the alias index built from that catalog.

## Interface
- `QualitySeverity` - Severity tier enum: `HIGH`, `MEDIUM`, `LOW`, `INFO`.
- `QualityCategory` - Defect category enum: `complexity`, `code_smell`,
  `maintainability`, `reliability`, `accessibility`.
- `QualityFinding` - Pydantic record for one finding: `id`, `rule_id`,
  `rspec_id`, `cwe_id`, `cwe_ids`, `owasp_category`, `category`, `severity`,
  `title`, `message`, `file_path`, `start_line`, `end_line`, `symbol_name`,
  `code_hash`, `metric_value`, `metric_threshold`, snippets, remediation
  guidance, `status`, `tag`, `rationale`, `reviewed_by`, `reviewed_at`, and
  `metadata`.
- `QualityAuditReport` - Aggregated report with severity counts,
  approval-state counts, average and maximum complexity, and the finding list.
- `QualityExceptionRecord` - Baseline exception entry; `from_finding` builds
  one from a `QualityFinding`.
- `QualityRuleSpec` - Frozen specification of a rule: IDs, severity, category,
  title, explanation, examples, `default_threshold`, `aliases`,
  `compliant_by_lang`, `cwe_ids`, and `owasp`.
- `QUALITY_RULES_CATALOG` - Mapping of canonical rule ID to `QualityRuleSpec`.
- `NATIVE_RULE_KEYS` - Frozenset of the canonical rule IDs in `QUALITY_RULES_CATALOG`
  [src/vibetrace/quality/rules_catalog.py:NATIVE_RULE_KEYS].
- `register_rule_alias(alias, canonical_id, overwrite=False)` - Registers a
  vendor alias in the module-level alias index.
- `resolve_canonical_rule_id(rule_or_alias)` - Maps a vendor alias or RSPEC ID
  to a canonical rule ID.
- `get_rule_spec(rule_or_alias)` - Retrieves the `QualityRuleSpec` for a
  canonical ID, RSPEC ID, or vendor alias.
- `QualityService` - Class with `audit_project`, `fix_project`,
  `apply_auto_comments`, `record_exception`, and `record_exceptions_batch`.
- `QualityBaselineManager` - Class with `get_baseline_path`, `load_baseline`,
  `apply_baseline_to_findings`, `record_exception`, and
  `record_exceptions_batch`; `QUALITY_BASELINE_FILE` names the
  `.vibetrace/quality.toml` baseline path
  [src/vibetrace/quality/baseline.py:QUALITY_BASELINE_FILE].
- `QualityCodemodApplier` - AST codemods `fix_s7503_async_def`,
  `fix_s1066_nested_ifs`, and `fix_file`.
- `DeclarativeQualityRule` - Frozen declarative rule record with `pattern`,
  `languages`, `negative_pattern`, and `compliant_snippet`.
- `BUILTIN_DECLARATIVE_QUALITY_RULES` - Built-in declarative rule list
  [src/vibetrace/quality/declarative_rules.py:BUILTIN_DECLARATIVE_QUALITY_RULES].
- `DeclarativeQualityRuleManager` - Class with `get_rules` and
  `scan_file_with_rules`.
- `compile_semgrep_pattern(semgrep_pat)` - Compiles a Semgrep pattern into a
  Python regular expression.
- `parse_semgrep_yaml(yaml_text)` - Parses Semgrep YAML rule definitions.
- `FunctionComplexity` - Cognitive and cyclomatic metrics for one function.
- `PythonCognitiveVisitor` - AST visitor computing SonarSource cognitive
  complexity.
- `analyze_python_complexities(file_path, source_code, threshold)` - Emits
  `python:S3776` findings.
- `PythonSmellsVisitor` - AST visitor detecting clean code smells.
- `analyze_python_smells(file_path, source_code)` - Emits `python:S1066`,
  `python:S107`, `python:S1186`, `python:S134`, `python:S1763`,
  `python:S1764`, `python:S2612`, `python:S3358`, `python:S4144`,
  `python:S5712`, `python:S5714`, and `python:S7503` findings.
- `StringLiteralVisitor` - AST visitor collecting string literal locations.
- `analyze_literal_duplications(file_path, source_code, threshold)` - Emits
  `python:S1192` findings.
- `compute_tree_cognitive_complexity(node, nesting)` - Cognitive complexity over
  a Tree-sitter node.
- `analyze_treesitter_quality(root_node, file_path, source_lines,
  complexity_threshold, language_prefix)` - Emits `S3776`, `S107`, `S134`,
  `S1066`, `S1192`, `S3358`, and `S3626` findings for non-Python languages.
- `CSSQualityAnalyzer` - Class with `analyze`; `analyze_css_quality(file_path,
  source_code)` emits `css:S7924`, `css:S9423`, `css:S9440`, and `css:S9449`.
- `DockerQualityAnalyzer` - Class with `analyze`; `analyze_docker_quality(
  file_path, source_code)` emits `docker:S6504`, `docker:S6505`, and
  `docker:S6506`.
- `K8sQualityAnalyzer` - Class with `analyze`; `analyze_k8s_quality(file_path,
  source_code)` emits `k8s:S101`, `k8s:S102`, `k8s:S103`, and `yaml:S104`.
- `SUPPORTED_EXTS`, `IGNORE_DIR_NAMES`, `IGNORE_PATH_PARTS` - Collector
  extension and ignore sets.
- `INLINE_IGNORE_RE` - Pattern for `vibetrace:ignore[<rule>]` directives.
- `is_suppressed_by_line(finding, line, rule_key)` - Tests one line for a
  suppression directive.
- `check_inline_suppression(finding, source_lines)` - Tests the two lines
  above and the finding line for a suppression directive.
- `resolve_candidate_paths(project_root, file_filter, file_list)` - Resolves
  candidate source files.
- `is_path_allowed(path, include_tests, project_root, spec)` - Filters paths
  by ignore segments, exclude patterns, and test-file naming.
- `collect_source_files(project_root, file_filter, file_list, include_tests)` -
  Resolves and filters the files to audit.
- `format_quality_audit_md(report, summary_only=False)` - Renders the report
  as Markdown.
- `format_quality_audit_sarif(report)` - Renders the report as OASIS SARIF
  2.1.0 JSON.
- [Adapters](./adapters/README.md) - External linter and type-checker
  sub-engine adapters.

## Invariants
- A finding is identified by `id` and anchored by a SHA-256 `code_hash` over
  the offending symbol or code slice
  [src/vibetrace/quality/models.py:QualityFinding].
- `QualityFinding.status` is one of `UNAPPROVED`, `APPROVED`, or
  `MODIFIED_CODE`, and defaults to `UNAPPROVED`
  [src/vibetrace/quality/models.py:QualityFinding].
- `QualityExceptionRecord` keeps `id` and `finding_id` in sync: each is copied
  from the other when only one is supplied
  [src/vibetrace/quality/models.py:QualityExceptionRecord].
- `QualityService.audit_project` deduplicates findings by the tuple
  `(file_path, start_line, canonical_rspec_short)`, keeping the first
  occurrence [src/vibetrace/quality/service.py:QualityService].
- `QualityService.audit_project` matches a file to an analyzer by extension:
  `.css` to the CSS analyzer, `.ts`/`.tsx`/`.js`/`.jsx`/`.go`/`.rs`/`.php`/
  `.phtml`/`.rb`/`.java`/`.c`/`.h`/`.cpp`/`.cc`/`.cxx`/`.hpp`/`.cs` to the
  Tree-sitter analyzer, `.yaml`/`.yml` to the Kubernetes analyzer,
  `dockerfile` to the Docker analyzer, and everything else to the Python
  analyzers [src/vibetrace/quality/service.py:_audit_single_file].
- Inline suppression is recognized on the finding line and the two preceding
  lines; `nosonar` suppresses any rule, while `noqa` and `vibetrace:ignore`
  suppress only when the named rule resolves to the finding's canonical rule
  key [src/vibetrace/quality/collector.py:is_suppressed_by_line],
  [src/vibetrace/quality/collector.py:check_inline_suppression].
- A finding suppressed by an inline comment is marked `APPROVED` with tag
  `inline-comment`, and that state survives baseline reconciliation
  [src/vibetrace/quality/collector.py:is_suppressed_by_line],
  [src/vibetrace/quality/baseline.py:QualityBaselineManager].
- Baseline matching keys on the finding ID, or on
  `rule_id:file_path:symbol_name` for function-scoped rules (`:S3776`, `:S107`,
  `:S1144`) that carry a named symbol
  [src/vibetrace/quality/baseline.py:QualityBaselineManager].
- A baseline entry whose recorded `code_hash` differs from the current finding
  hash yields status `MODIFIED_CODE` rather than `APPROVED`
  [src/vibetrace/quality/baseline.py:QualityBaselineManager].
- `QualityService.audit_project` enriches findings with CWE and OWASP data
  from the rule catalog and from `CweTaxonomyManager` only when the finding
  does not already carry it [src/vibetrace/quality/service.py:QualityService].
- `collect_source_files` defaults to the project's `src` directory when it
  exists, and otherwise to the project root
  [src/vibetrace/quality/collector.py:resolve_candidate_paths].
- Test files and test directories are excluded unless `include_tests` is set,
  either directly or through `config.quality.include_tests`
  [src/vibetrace/quality/collector.py:is_path_allowed],
  [src/vibetrace/quality/collector.py:collect_source_files].
- Candidate and scanned paths must resolve inside the project root; paths
  outside it are rejected
  [src/vibetrace/quality/collector.py:resolve_candidate_paths],
  [src/vibetrace/quality/declarative_rules.py:DeclarativeQualityRuleManager].
- `DeclarativeQualityRuleManager.get_rules` always returns the built-in rule
  pack, and adds cached SQLite rules only when the cache entry is younger than
  `DEFAULT_QUALITY_RULES_TTL_DAYS` (7 days) unless `force_update` is set
  [src/vibetrace/quality/declarative_rules.py:DeclarativeQualityRuleManager].
- Declarative rules apply only to files whose detected language appears in the
  rule's `languages` tuple
  [src/vibetrace/quality/declarative_rules.py:DeclarativeQualityRuleManager].
- A declarative rule match is skipped when the matched snippet starts with a
  comment marker or when the snippet matches the rule's `negative_pattern`
  [src/vibetrace/quality/declarative_rules.py:DeclarativeQualityRuleManager].
- `QualityCodemodApplier.fix_s7503_async_def` leaves `__aenter__` and
  `__aexit__` untouched, skips functions whose decorators mark them as
  framework handlers, and skips any function containing an `await`
  [src/vibetrace/quality/codemods.py:QualityCodemodApplier].
- `QualityCodemodApplier.fix_s1066_nested_ifs` merges only an `if` whose body
  is a single `if` and where neither the outer nor the inner branch has an
  `else`; generated code is re-parsed before it is returned
  [src/vibetrace/quality/codemods.py:QualityCodemodApplier].
- `QualityService.apply_auto_comments` only edits files that resolve inside
  the project root, and inserts comments from the last line upward so earlier
  line numbers stay valid
  [src/vibetrace/quality/service.py:QualityService].
- `QualityService.audit_project` runs the TypeScript engine only when
  `typecheck` is requested, and runs the SonarLint engine only when
  `enable_sonar` is set or `config.quality.enable_sonar` is true
  [src/vibetrace/quality/service.py:QualityService].

## Side Effects
- Reads project source files from disk during audits and inline suppression
  checks [src/vibetrace/quality/service.py:_audit_single_file],
  [src/vibetrace/quality/service.py:QualityService].
- Walks the project tree with `os.walk` to discover candidate source files,
  pruning ignored directories
  [src/vibetrace/quality/collector.py:_walk_candidate_paths].
- Writes and rewrites `.vibetrace/quality.toml` when recording baseline
  exceptions; `record_exceptions_batch` rewrites the file once for the whole
  batch [src/vibetrace/quality/baseline.py:QualityBaselineManager].
- Creates the `.vibetrace/` directory when a baseline file is first written
  [src/vibetrace/quality/baseline.py:QualityBaselineManager].
- Rewrites Python source files in place when applying codemods
  [src/vibetrace/quality/codemods.py:QualityCodemodApplier].
- Rewrites source files in place when injecting `vibetrace:ignore` suppression
  comments [src/vibetrace/quality/service.py:QualityService].
- Creates and reads the `quality_declarative_rules_cache` SQLite table through
  the `IndexerDB` connection
  [src/vibetrace/quality/declarative_rules.py:DeclarativeQualityRuleManager].
- Runs external linters and type checkers as subprocesses through the adapter
  sub-engines; `fix_project` invokes the fix modes that mutate source files
  [src/vibetrace/quality/service.py:QualityService],
  [src/vibetrace/quality/adapters/README.md].
- Reads call-graph data from `IndexerDB` to detect uncalled private functions
  and to fill in missing symbol names
  [src/vibetrace/quality/service.py:QualityService].
- Reads project configuration through `vibetrace.config.load_config` for
  quality gates, exclude patterns, linter modes, and the SonarLint toggle
  [src/vibetrace/quality/collector.py:collect_source_files],
  [src/vibetrace/quality/service.py:QualityService].
- `TRUSTED_QUALITY_YAML_FEEDS` declares Semgrep Community Registry feed URLs
  but is not read by any code path in this package, so no network request is
  made [src/vibetrace/quality/declarative_rules.py:DeclarativeQualityRuleManager].

## Verification
- Run: `uv run pytest`
