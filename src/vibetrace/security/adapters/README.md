# Security Adapters (`src/vibetrace/security/adapters`)

## Overview
Adapters that invoke external scanning CLIs when present on `$PATH` and normalize their
SARIF 2.1.0 output into VibeTrace `SecurityFinding`s via `SarifIngestEngine`. Trivy covers
container images, OS packages, and IaC misconfigurations; Semgrep covers deep SAST/taint.

## Interface
- `TrivyAdapter` — re-exported by `[src/vibetrace/security/adapters/__init__.py]`; defined at `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter]` (`is_available`, `has_container_configs`, `scan_filesystem`, `scan_image`)
- `SemgrepAdapter` — `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter]` (`is_available`, `scan_project`) and `BUNDLED_SEMGREP_PRESET_PATH` (`[src/vibetrace/resources/presets/semgrep_security.yml]`)

## Invariants
- Adapters are no-ops returning `[]` when their CLI is not on `PATH` — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter.is_available]`, `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.is_available]`
- When `config == "auto"` and `BUNDLED_SEMGREP_PRESET_PATH` exists, `SemgrepAdapter.scan_project` passes `--metrics=off --config <BUNDLED_SEMGREP_PRESET_PATH>` (plus `--experimental` on Windows) to stay offline-safe — `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`
- Trivy image findings with empty/`.` `file_path` are rewritten to `image:<tag>` and tagged with `metadata["image_tag"]` — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter.scan_image]`
- Every emitted finding is tagged `metadata["scanner"]` = `"semgrep"` or `"trivy"` (plus `trivy_scope` for Trivy) — `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`, `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter._run_and_parse]`
- Subprocess timeouts abort the scan and return `[]` rather than raising — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter._run_and_parse]`, `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`

## Side Effects
- Subprocess execution of `trivy fs` / `trivy image` and `semgrep scan` (with `--sarif --quiet --metrics=off`) — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter._run_and_parse]`, `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`
- `TrivyAdapter` may update its vulnerability DB over the network; `SemgrepAdapter` runs offline with `--metrics=off` against `BUNDLED_SEMGREP_PRESET_PATH` when `config == "auto"`

## Verification
- Run: `uv run pytest`
