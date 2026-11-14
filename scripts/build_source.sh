#!/usr/bin/env bash
# Build VibeTrace (including the native Rust core) from source on Linux/macOS.
set -euo pipefail

require_cmd() {
    if ! command -v "$1" >/dev/null 2>&1; then
        echo "Error: Missing required tool '$1'. $2" >&2
        exit 1
    fi
}

echo "==> Checking build prerequisites..."
require_cmd uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
require_cmd rustc "Install Rust: https://rustup.rs/"
require_cmd cargo "Install Rust: https://rustup.rs/"

echo "==> Syncing Python environment with uv..."
uv sync

echo "==> Building native Rust core (vibetrace._rust_core)..."
uv run maturin develop --release

echo "==> Verifying vibetrace._rust_core..."
uv run python -c "import vibetrace._rust_core as rc; print('OK:', rc.__file__)"
echo "Build complete!"
