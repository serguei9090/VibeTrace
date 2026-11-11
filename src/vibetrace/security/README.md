# VibeTrace Security (`src/vibetrace/security`)

## Overview
Component package for VibeTrace's security & vulnerability analysis. It scans source
ASTs for anti-patterns, verifies taint reachability from untrusted entry points to sinks,
scans git history for leaked secrets, IaC manifests for misconfigurations, lockfiles for
known CVEs (OSV.dev), ingests external SARIF reports, applies a Git-tracked exception
baseline, and renders Markdown/SARIF reports. The package entry point re-exports the
core model and service symbols.

## Interface
- `Severity`, `FindingCategory`, `SecurityFinding`, `SecurityAuditReport` — `[src/vibetrace/security/models.py:SecurityFinding]`, re-exported by `[src/vibetrace/security/__init__.py]`
- `SecurityService` (`audit_project`, `record_exception`, `build_remediation_bundle`) — `[src/vibetrace/security/service.py:SecurityService]`
- `AstSecurityScanner` (`scan_file`) — `[src/vibetrace/security/ast_scanner.py:AstSecurityScanner]`
- `RuleCatalogManager`, `DeclarativeRule`, `BUILTIN_DECLARATIVE_RULES`, `compile_semgrep_pattern`, `parse_semgrep_yaml`, `PINNED_CATALOG_REF`, `DEFAULT_RULES_FEED_URL`, `BUNDLED_SECURITY_CATALOG_PATH`, `TRUSTED_COMMUNITY_YAML_FEEDS` — `[src/vibetrace/security/rule_sync.py:RuleCatalogManager]`
- `SecurityRuleSpec`, `RULES_CATALOG` — `[src/vibetrace/security/rules.py:RULES_CATALOG]`
- `TaintEngine`, `SANITIZERS`, `SHARED_SANITIZERS`, `PYTHON_SANITIZERS`, `JS_TS_SANITIZERS`, `GO_SANITIZERS`, `CSHARP_SANITIZERS`, `MAX_DEPTH`, `is_source`, `get_scoped_sanitizers`, `TAINT_CWES` — `[src/vibetrace/security/taint_engine.py:TaintEngine]`
- `TaintContractManager`, `FunctionTaintContract` — `[src/vibetrace/security/contracts.py:TaintContractManager]`
- `verify_sink_taint_flow`, `verify_interprocedural_taint_flow`, `clear_taint_file_cache` — `[src/vibetrace/security/dataflow.py:verify_interprocedural_taint_flow]`
- `analyze_regex_quality`, `RegexQualityAnalyzer` — `[src/vibetrace/security/regex_analyzer.py:analyze_regex_quality]`
- `IacScanner` (`scan_project`) — `[src/vibetrace/security/iac_scanner.py:IacScanner]`
- `GitSecretScanner`, `shannon_entropy` — `[src/vibetrace/security/git_secret_scanner.py:GitSecretScanner]`
- `LockfileParser`, `DependencyPackage` — `[src/vibetrace/security/lockfile_parser.py:LockfileParser]`
- `OsvClient` (`query_vulnerabilities`), `OSV_QUERY_URL`, `OSV_QUERYBATCH_URL` — `[src/vibetrace/security/osv_client.py:OsvClient]`
- `DependencyGraph`, `DependencyGraphBuilder`, `PackageNode`, `DependencyChain`, `DiamondConflict`, `DependencyGraphReport`, `normalize_package_name` — `[src/vibetrace/security/dep_graph.py:DependencyGraphBuilder]`
- `DependencyAutoPatcher`, `check_package_reachability`, `DependencyPatchAction`, `AutoPatchReport` — `[src/vibetrace/security/auto_patcher.py:DependencyAutoPatcher]`
- `SecurityBaselineManager`, `SECURITY_BASELINE_FILE` — `[src/vibetrace/security/baseline.py:SecurityBaselineManager]`
- `SarifIngestEngine` (`parse_sarif_file`, `parse_sarif_dict`) — `[src/vibetrace/security/sarif_ingest.py:SarifIngestEngine]`
- `PRTaintBot`, `PRTaintReport`, `PRTaintComment`, `get_pr_modified_ranges`, `get_git_head_sha`, `synthesize_remediation`, `build_comment_body` — `[src/vibetrace/security/pr_bot.py:PRTaintBot]`
- `format_security_audit_md`, `format_security_audit_sarif` — `[src/vibetrace/security/formatters.py:format_security_audit_sarif]`
- Child package: [adapters](./adapters/README.md) — external CLI scanner adapters (Semgrep, Trivy)

## Invariants
- Finding IDs are deterministic SHA-256 fingerprints (`SEC-<CWE>-<hash12>`) so baseline matching is stable across runs — `[src/vibetrace/security/ast_scanner.py:_calc_code_hash]`
- Baseline exceptions are keyed by finding `id` and `rule_id:file_path`; a stored `code_hash` that no longer matches flips the finding to `MODIFIED_CODE` — `[src/vibetrace/security/baseline.py:SecurityBaselineManager.apply_baseline_to_findings]`
- Inline `nosec` / `vibetrace:ignore` comments mark findings `APPROVED` with tag and rationale — `[src/vibetrace/security/ast_scanner.py:AstSecurityScanner.scan_file]`
- SARIF input paths must stay inside the project root; escaping paths raise `ValueError` — `[src/vibetrace/security/service.py:SecurityService.audit_project]`, path sanitization in `[src/vibetrace/security/sarif_ingest.py:SarifIngestEngine._parse_single_result]`
- Remote rule-catalog payloads are verified against a pinned SHA-256 before being trusted — `[src/vibetrace/security/rule_sync.py:EXPECTED_FEED_SHA256]`
- AST findings are escalated to `TAINT_REACHABILITY` with severity bumped one level only when a verified source-to-sink path exists — `[src/vibetrace/security/taint_engine.py:TaintEngine._process_candidate]`
- Test files are excluded from audits unless `include_tests` is set — `[src/vibetrace/security/service.py:SecurityService._is_test_path]`
- OSV cache entries are only reused within `CACHE_TTL_SECONDS` (24 h) — `[src/vibetrace/security/osv_client.py:CACHE_TTL_SECONDS]`
- Rule-catalog sync TTL is read from `.vibetrace/security.toml` `[sync]`, default `DEFAULT_RULES_TTL_DAYS` (7) — `[src/vibetrace/security/rule_sync.py:DEFAULT_RULES_TTL_DAYS]`
- SARIF findings with 0 incoming callers and no entry-point path are silenced as `unreachable_dead_code` — `[src/vibetrace/security/sarif_ingest.py:SarifIngestEngine._enrich_with_reachability]`

## Side Effects
- Writes `.vibetrace/security.toml` baseline exceptions — `[src/vibetrace/security/baseline.py:SecurityBaselineManager.record_exception]`
- SQLite writes: `function_taint_contracts` table — `[src/vibetrace/security/contracts.py:TaintContractManager._init_table]`; `osv_cve_cache` — `[src/vibetrace/security/osv_client.py:OsvClient._init_cache_table]`; `security_rules_cache` — `[src/vibetrace/security/rule_sync.py:RuleCatalogManager._ensure_table]`
- Network: POST to OSV.dev `querybatch`/`query` — `[src/vibetrace/security/osv_client.py:OsvClient._fetch_from_osv]`; remote rule catalog + pinned Semgrep community YAML feeds — `[src/vibetrace/security/rule_sync.py:RuleCatalogManager._refresh_from_feed]`; GitHub PR review POST — `[src/vibetrace/security/pr_bot.py:PRTaintBot.post_github_review]`
- Subprocess: `git ls-files/diff/log/rev-parse` — `[src/vibetrace/security/git_secret_scanner.py:GitSecretScanner.scan_staged]`, `[src/vibetrace/security/pr_bot.py:get_pr_modified_ranges]`; native package-manager upgrade commands — `[src/vibetrace/security/auto_patcher.py:DependencyAutoPatcher.apply_patch_plan]`
- Filesystem writes: `PRTaintBot.apply_remediations` rewrites source files in place — `[src/vibetrace/security/pr_bot.py:PRTaintBot.apply_remediations]`
- In-memory caches: `_load_file_lines_and_contracts` LRU (cleared after each `TaintEngine.analyze`) — `[src/vibetrace/security/dataflow.py:clear_taint_file_cache]`; `_LOCKFILE_HASH_CACHE` module dict — `[src/vibetrace/security/lockfile_parser.py:LockfileParser._cached_parse]`
- Disk reads: source files, lockfiles, and IaC manifests under the project root by all scanners

## Verification
- Run: `uv run pytest`
