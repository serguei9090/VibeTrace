<#
.SYNOPSIS
    Build VibeTrace (including the native Rust core) from source on Windows.
.DESCRIPTION
    Checks for required build tools (uv, rustc, cargo), syncs the Python
    environment, compiles vibetrace._rust_core via maturin, and verifies the import.
#>
$ErrorActionPreference = "Stop"

function Require-Command($Name, $InstallHint) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Write-Error "Missing required tool '$Name'. $InstallHint"
        exit 1
    }
}

Write-Host "==> Checking build prerequisites..."
Require-Command "uv" "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
Require-Command "rustc" "Install Rust: https://rustup.rs/"
Require-Command "cargo" "Install Rust: https://rustup.rs/"

Write-Host "==> Syncing Python environment with uv..."
uv sync

Write-Host "==> Building native Rust core (vibetrace._rust_core)..."
uv run maturin develop --release

Write-Host "==> Verifying vibetrace._rust_core..."
uv run python -c "import vibetrace._rust_core as rc; print('OK:', rc.__file__)"
Write-Host "Build complete!"
