import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from vibetrace.server_api import app


def test_vscode_extension_manifest_contract():
    """Verify extensions/vscode/package.json registers all necessary commands."""
    pkg_path = Path("extensions/vscode/package.json")
    assert pkg_path.is_file(), "extensions/vscode/package.json must exist"

    with open(pkg_path, encoding="utf-8") as f:
        pkg = json.load(f)

    assert pkg["name"] == "vibetrace-vscode"
    assert "engines" in pkg
    assert "vscode" in pkg["engines"]

    # Verify commands
    commands = {cmd["command"] for cmd in pkg.get("contributes", {}).get("commands", [])}
    expected_commands = {
        "vibetrace.inspectCallers",
        "vibetrace.copySlice",
        "vibetrace.bundleContext",
        "vibetrace.openMapRoom",
        "vibetrace.refreshProject",
        "vibetrace.reindex",
    }
    assert expected_commands.issubset(commands), f"Missing commands: {expected_commands - commands}"

    # Verify views
    views = pkg.get("contributes", {}).get("views", {}).get("vibetrace-explorer", [])
    view_ids = {v["id"] for v in views}
    assert "vibetrace.mapRoomView" in view_ids

    # Verify configuration schema
    props = pkg.get("contributes", {}).get("configuration", {}).get("properties", {})
    assert "vibetrace.daemonUrl" in props
    assert "vibetrace.wsUrl" in props
    assert "vibetrace.enableCodeLens" in props
    assert "vibetrace.enableDiagnostics" in props


def test_vscode_extension_bundle_artifact():
    """Verify extensions/vscode/dist/extension.js exports activate/deactivate if built."""
    dist_path = Path("extensions/vscode/dist/extension.js")
    if not dist_path.is_file():
        pytest.skip("VSCode extension not compiled in environment (dist/extension.js absent)")
    content = dist_path.read_text(encoding="utf-8")
    assert len(content) > 1000, "dist/extension.js bundle must not be empty"
    assert "activate" in content, "Bundle must contain activate function"
    assert "deactivate" in content, "Bundle must contain deactivate function"


def test_vscode_client_api_endpoint_parity():
    """Verify that every endpoint invoked by VibeTraceApiClient is reachable."""
    client = TestClient(app)

    # 1. /api/graph (daemon health & initial topology check)
    resp = client.get("/api/graph")
    assert resp.status_code == 200

    # 2. /api/project (federation status)
    resp = client.get("/api/project")
    # 200 if manifest found or 404 if no project file in current test root
    assert resp.status_code in (200, 404)

    # 3. /api/project/links
    resp = client.get("/api/project/links")
    assert resp.status_code in (200, 404)

    # 4. /api/vibe/guard (validation error if symbol missing)
    resp = client.get("/api/vibe/guard")
    assert resp.status_code == 422
