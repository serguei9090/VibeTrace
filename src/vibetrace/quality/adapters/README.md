# Quality Linter Sub-Engine Adapters

## Overview
Adapters for native external linters and type checkers. Each adapter runs one
toolchain in a subprocess, parses its machine-readable output, and normalizes
results into `QualityFinding` records that use SonarSource RSPEC identifiers.

`LinterSubEngine` defines the shared contract: probe tool availability,
audit a single file, and audit a batch of files. Adapters resolve their runner
from the project workspace, `~/.vibetrace/bin`, or `PATH`; several also honor
`vibetrace.config.resolve_linter_mode` to choose between a project config and
the bundled strict preset.

Rule normalization is per-adapter mapping tables keyed by vendor rule ID:
`BIOME_TO_SONAR` (Biome rule path), `GOLANG_TO_SONAR` (golangci-lint linter
name), `PHPSTAN_TO_SONAR` (PHPStan identifier), `TSC_TO_SONAR` (TypeScript
diagnostic code), `RUBOCOP_TO_SONAR` (RuboCop cop name), `CLIPPY_TO_SONAR`
(Clippy lint name), `SHELL_TO_SONAR` (ShellCheck numeric code), and
`SQL_TO_SONAR` (SQLFluff rule code). Unmapped vendor rules are dropped rather
than reported under an invented identifier.

## Interface
- `LinterSubEngine` - Abstract base class with `is_available(project_root)`,
  `audit_file(file_path, project_root)`, and
  `audit_project(project_root, file_list)`.
- `extract_json_payload(raw)` - Isolates the outermost JSON object or array
  from mixed tool output.
- `BiomeAdapter` - JavaScript/TypeScript/JSX/JSON/CSS via Biome SARIF output.
  Exposes `SUPPORTED_EXTENSIONS`, `audit_file`, `audit_project`, `fix_files`;
  `BUNDLED_PRESET_PATH` points at the bundled `biome_sonar.json` preset.
- `GolangLintAdapter` - Go via `golangci-lint` JSON output. Exposes
  `SUPPORTED_EXTENSIONS`, `audit_file`, `audit_project`, `fix_files`.
- `PhpStanAdapter` - PHP via PHPStan JSON output, with fixer discovery for
  auto-fix. Exposes `SUPPORTED_EXTENSIONS`, `audit_file`, `audit_project`,
  `fix_files`.
- `PyrightAdapter` - Python type diagnostics via Pyright JSON output. Exposes
  `audit_file`, `audit_project`; no fix mode. `BUNDLED_PRESET_PATH` points at the
  bundled `pyright_sonar.json` preset.
- `RuffAdapter` - Python lint via Ruff JSON output. Exposes `audit_file`,
  `audit_project`, `fix_files`. `BUNDLED_PRESET_PATH` points at the bundled
  `ruff_sonar.toml` preset.
- `RuboCopAdapter` - Ruby via RuboCop JSON output. Exposes
  `parse_rubocop_output`, `audit_file`, `audit_project`, `fix_files`.
- `CargoClippyAdapter` - Rust via `cargo clippy` JSON messages. Exposes
  `SUPPORTED_EXTENSIONS`, `audit_file`, `audit_project`, `fix_files`.
- `ShellCheckAdapter` - Shell scripts via ShellCheck JSON output. Exposes
  `SUPPORTED_EXTENSIONS`, `parse_shellcheck_output`, `audit_file`,
  `audit_project`; no fix mode.
- `SonarLintAdapter` - Optional Tier-2 SonarLint Language Server session.
  Exposes `audit_file`, `audit_project`, `scan_security_findings`.
- `SqlFluffAdapter` - SQL via SQLFluff JSON output. Exposes
  `SUPPORTED_EXTENSIONS`, `parse_sqlfluff_output`, `audit_file`,
  `audit_project`, `fix_files`.
- `TypeScriptAdapter` - TypeScript via `tsc --noEmit` diagnostics. Exposes
  `SUPPORTED_EXTENSIONS`, `audit_file`, `audit_project`; no fix mode.
- `SonarRuntimePaths` - Resolved filesystem paths for the SonarLint runtime.
- `resolve_sonar_runtime(custom_home)` - Locates a SonarLint distribution and
  its analyzer JARs under VibeTrace tools or local IDE extensions.
- `extract_rspec_short(rule_id)` - Reduces a rule ID to its short `S<digits>`
  form.
- `sys_platform_name()` - Platform token reported to the SonarLint server.

## Invariants
- `LinterSubEngine` subclasses implement `is_available`, `audit_file`, and
  `audit_project`; `audit_file` and `audit_project` return
  `list[QualityFinding]`.
- `LinterSubEngine.is_available` reports whether the underlying tool binary or
  runner is executable in the workspace
  [src/vibetrace/quality/adapters/base.py:LinterSubEngine].
- A finding is emitted only when the vendor rule has an entry in that
  adapter's mapping table; the table supplies the canonical RSPEC short code,
  RSPEC ID, `QualityCategory`, and `QualitySeverity`
  [src/vibetrace/quality/adapters/biome.py:BIOME_TO_SONAR],
  [src/vibetrace/quality/adapters/golang_lint.py:GolangLintAdapter],
  [src/vibetrace/quality/adapters/php_stan.py:PHPSTAN_TO_SONAR],
  [src/vibetrace/quality/adapters/typescript_tsc.py:TSC_TO_SONAR].
- Every finding carries a SHA-256 `code_hash` and an ID of the form
  `QUAL-<ENGINE_OR_SHORTCODE>-<hash prefix>`; adapters use engine-specific
  prefixes such as `QUAL-BIOME-`, `QUAL-TYPE-`, `QUAL-TSC-`, `QUAL-SQL-`, and
  `QUAL-SC<CODE>-`
  [src/vibetrace/quality/adapters/biome.py:BiomeAdapter],
  [src/vibetrace/quality/adapters/python_ruff.py:RuffAdapter],
  [src/vibetrace/quality/adapters/sonar_lint.py:SonarLintAdapter].
- Adapter config resolution prefers a project-local override under
  `.vibetrace/config/` and otherwise falls back to the bundled preset
  [src/vibetrace/quality/adapters/python_ruff.py:RuffAdapter],
  [src/vibetrace/quality/adapters/python_pyright.py:PyrightAdapter],
  [src/vibetrace/quality/adapters/biome.py:BiomeAdapter].
- Runner resolution is cached per adapter instance, so a runner is probed at
  most once per instance
  [src/vibetrace/quality/adapters/golang_lint.py:GolangLintAdapter].
- `audit_project` filters its input file list to the extensions that adapter
  supports before running the tool
  [src/vibetrace/quality/adapters/biome.py:BiomeAdapter],
  [src/vibetrace/quality/adapters/rust_clippy.py:CargoClippyAdapter].
- `SonarLintAdapter` reports availability only when a SonarLint runtime and a
  Java binary are both resolvable
  [src/vibetrace/quality/adapters/sonar_lint.py:SonarLintAdapter],
  [src/vibetrace/quality/adapters/sonar_lint.py:resolve_sonar_runtime].
- The SonarLint JSON-RPC session is confined to stdio and answers only
  `sonarlint/listFilesInFolder`, `workspace/configuration`,
  `sonarlint/isOpenInEditor`, `sonarlint/shouldAnalyseFile`, and
  `sonarlint/filterOutExcludedFiles`; telemetry is disabled in the
  configuration response
  [src/vibetrace/quality/adapters/sonar_lint.py:_SonarJsonRpcSession].
- `scan_security_findings` converts a quality finding into a `SecurityFinding`
  only when its short code is in `_SECURITY_RULE_CODES` or it carries a CWE
  mapping
  [src/vibetrace/quality/adapters/sonar_lint.py:SonarLintAdapter].

## Side Effects
- Each adapter spawns its external tool as a subprocess with `cwd` set to the
  project root and a per-adapter timeout; `OSError` and
  `subprocess.SubprocessError` are caught and converted to empty or failed
  results rather than propagating
  [src/vibetrace/quality/adapters/biome.py:BiomeAdapter],
  [src/vibetrace/quality/adapters/golang_lint.py:GolangLintAdapter],
  [src/vibetrace/quality/adapters/php_stan.py:PhpStanAdapter],
  [src/vibetrace/quality/adapters/python_pyright.py:PyrightAdapter],
  [src/vibetrace/quality/adapters/python_ruff.py:RuffAdapter],
  [src/vibetrace/quality/adapters/rubocop.py:RuboCopAdapter],
  [src/vibetrace/quality/adapters/rust_clippy.py:CargoClippyAdapter],
  [src/vibetrace/quality/adapters/shellcheck.py:ShellCheckAdapter],
  [src/vibetrace/quality/adapters/sql_fluff.py:SqlFluffAdapter],
  [src/vibetrace/quality/adapters/typescript_tsc.py:TypeScriptAdapter].
- `fix_files` mutates files in place through the underlying tool: Ruff
  `check --fix` plus `format`, Biome `check --write`, golangci-lint `run
  --fix`, Clippy `clippy --fix --allow-dirty --allow-staged`, RuboCop `-a`,
  SQLFluff `fix --force`, and PHPStan through a discovered fixer
  [src/vibetrace/quality/adapters/python_ruff.py:RuffAdapter],
  [src/vibetrace/quality/adapters/biome.py:BiomeAdapter],
  [src/vibetrace/quality/adapters/golang_lint.py:GolangLintAdapter],
  [src/vibetrace/quality/adapters/rust_clippy.py:CargoClippyAdapter],
  [src/vibetrace/quality/adapters/rubocop.py:RuboCopAdapter],
  [src/vibetrace/quality/adapters/sql_fluff.py:SqlFluffAdapter],
  [src/vibetrace/quality/adapters/php_stan.py:PhpStanAdapter].
- Runner discovery reads `PATH`, `GOPATH`, `HOME`, and the workspace, and
  probes candidates with a version subprocess
  [src/vibetrace/quality/adapters/golang_lint.py:GolangLintAdapter],
  [src/vibetrace/quality/adapters/biome.py:BiomeAdapter],
  [src/vibetrace/quality/adapters/rubocop.py:RuboCopAdapter].
- `TypeScriptAdapter.audit_project` creates `.vibetrace/cache/` and writes
  `tsc.tsbuildinfo` as an incremental build cache
  [src/vibetrace/quality/adapters/typescript_tsc.py:TypeScriptAdapter].
- The SonarLint session creates a work directory and telemetry directory under
  `~/.vibetrace/tools/sonar/work/`, and spawns `java -Duser.home=<work_dir>
  -jar sonarlint-ls.jar -stdio` as a child process that is terminated on
  session exit
  [src/vibetrace/quality/adapters/sonar_lint.py:_SonarJsonRpcSession],
  [src/vibetrace/quality/adapters/sonar_lint.py:resolve_sonar_runtime].
- The SonarLint session reads target file contents from disk and sends them to
  the server over the stdio pipe
  [src/vibetrace/quality/adapters/sonar_lint.py:_SonarJsonRpcSession].
- The SonarLint session uses a background reader thread and a write lock to
  serialize JSON-RPC frames
  [src/vibetrace/quality/adapters/sonar_lint.py:_SonarJsonRpcSession].

## Verification
- Run: `uv run pytest`
