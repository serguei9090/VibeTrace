# Security Adapters (`src/vibetrace/security/adapters`)

## Overview
Adapters that invoke external scanning CLIs when present on `$PATH` and normalize their
SARIF 2.1.0 output into VibeTrace `SecurityFinding`s via `SarifIngestEngine`. Trivy covers
container images, OS packages, and IaC misconfigurations; Semgrep covers deep SAST/taint.

## Interface
- `TrivyAdapter` — re-exported by `[src/vibetrace/security/adapters/__init__.py]`; defined at `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter]` (`is_available`, `has_container_configs`, `scan_filesystem`, `scan_image`)
- `SemgrepAdapter` — `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter]` (`is_available`, `scan_project`)

## Invariants
- Adapters are no-ops returning `[]` when their CLI is not on `PATH` — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter.is_available]`, `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.is_available]`
- Semgrep target file lists are capped at the first 100 files — `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`
- Trivy image findings with empty/`.` `file_path` are rewritten to `image:<tag>` and tagged with `metadata["image_tag"]` — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter.scan_image]`
- Every emitted finding is tagged `metadata["scanner"]` = `"semgrep"` or `"trivy"` (plus `trivy_scope` for Trivy) — `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`, `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter._run_and_parse]`
- Subprocess timeouts abort the scan and return `[]` rather than raising — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter._run_and_parse]`, `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`

## Side Effects
- Subprocess execution of `trivy fs` / `trivy image` and `semgrep scan` (with `--sarif --quiet`) — `[src/vibetrace/security/adapters/trivy.py:TrivyAdapter._run_and_parse]`, `[src/vibetrace/security/adapters/semgrep.py:SemgrepAdapter.scan_project]`
- Network access is performed by the external CLIs themselves (Trivy vulnerability DB, Semgrep registry), not by adapter code

## Verification
- Run: `uv run pytest`
