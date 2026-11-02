"""Tests for Transitive Dependency Graph Resolution & Root-Cause Visualizer."""

import argparse
import json
from pathlib import Path

from vibetrace.cli import build_parser
from vibetrace.cli_deps import _cmd_deps_conflicts, _cmd_deps_tree, _cmd_deps_why
from vibetrace.security.dep_graph import (
    DependencyGraph,
    DependencyGraphBuilder,
    PackageNode,
    normalize_package_name,
)


def test_normalize_package_name():
    """Verify package name normalization rules across ecosystems."""
    assert normalize_package_name("Typing_Extensions", "PyPI") == "typing-extensions"
    assert normalize_package_name("pypdf.core", "PyPI") == "pypdf-core"
    assert normalize_package_name("Express", "npm") == "express"
    assert normalize_package_name("Tokio", "Cargo") == "tokio"


def test_uv_lock_parsing_and_causation_chains(tmp_path: Path):
    """Verify uv.lock parsing, direct root mapping from pyproject.toml, and inverted BFS."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "my-service"
version = "0.1.0"
dependencies = [
    "httpx>=0.26.0",
    "fastapi>=0.110.0",
]
""",
        encoding="utf-8",
    )

    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text(
        """
version = 1
revision = 1

[[package]]
name = "fastapi"
version = "0.110.0"
dependencies = [
    { name = "starlette" },
]

[[package]]
name = "starlette"
version = "0.36.3"
dependencies = [
    { name = "anyio" },
]

[[package]]
name = "anyio"
version = "4.3.0"
dependencies = [
    { name = "idna" },
]

[[package]]
name = "httpx"
version = "0.27.0"
dependencies = [
    { name = "httpcore" },
]

[[package]]
name = "httpcore"
version = "1.0.5"
dependencies = [
    { name = "certifi" },
]

[[package]]
name = "certifi"
version = "2024.2.2"

[[package]]
name = "idna"
version = "3.6"
""",
        encoding="utf-8",
    )

    graph = DependencyGraphBuilder.build(tmp_path)

    assert "httpx" in graph.direct_roots
    assert "fastapi" in graph.direct_roots
    assert "certifi" not in graph.direct_roots

    # Resolve causation for transitive package certifi
    chains = graph.resolve_why("certifi")
    assert len(chains) == 1
    c = chains[0]
    assert c.root_package == "httpx"
    assert c.target_package == "certifi"
    assert c.depth == 2
    assert c.path == ["httpx", "httpcore", "certifi"]

    # Direct package check
    direct_chains = graph.resolve_why("httpx")
    assert len(direct_chains) == 1
    assert direct_chains[0].depth == 0
    assert direct_chains[0].path == ["httpx"]

    # Unknown package
    assert graph.resolve_why("nonexistent-pkg") == []


def test_package_lock_json_resolution(tmp_path: Path):
    """Verify npm package-lock.json parsing and inverted chain resolution."""
    pkg_json = tmp_path / "package.json"
    pkg_json.write_text(
        json.dumps(
            {
                "name": "web-app",
                "dependencies": {"express": "^4.18.2"},
            }
        ),
        encoding="utf-8",
    )

    lock_json = tmp_path / "package-lock.json"
    lock_json.write_text(
        json.dumps(
            {
                "name": "web-app",
                "lockfileVersion": 3,
                "packages": {
                    "node_modules/express": {
                        "version": "4.18.2",
                        "dependencies": {"body-parser": "^1.20.1"},
                    },
                    "node_modules/body-parser": {
                        "version": "1.20.1",
                        "dependencies": {"qs": "6.11.0"},
                    },
                    "node_modules/qs": {
                        "version": "6.11.0",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    graph = DependencyGraphBuilder.build(tmp_path)
    assert "express" in graph.direct_roots
    assert "qs" not in graph.direct_roots

    chains = graph.resolve_why("qs")
    assert len(chains) == 1
    assert chains[0].path == ["express", "body-parser", "qs"]
    assert chains[0].depth == 2


def test_cargo_lock_resolution(tmp_path: Path):
    """Verify Cargo.lock parsing and transitive chain resolution."""
    cargo_toml = tmp_path / "Cargo.toml"
    cargo_toml.write_text(
        """
[package]
name = "rust-service"
version = "0.1.0"

[dependencies]
axum = "0.7.4"
""",
        encoding="utf-8",
    )

    cargo_lock = tmp_path / "Cargo.lock"
    cargo_lock.write_text(
        """
version = 3

[[package]]
name = "axum"
version = "0.7.4"
dependencies = [
    "tower 0.4.13",
]

[[package]]
name = "tower"
version = "0.4.13"
dependencies = [
    "tokio 1.35.0",
]

[[package]]
name = "tokio"
version = "1.35.0"
""",
        encoding="utf-8",
    )

    graph = DependencyGraphBuilder.build(tmp_path)
    assert "axum" in graph.direct_roots

    chains = graph.resolve_why("tokio")
    assert len(chains) == 1
    assert chains[0].path == ["axum", "tower", "tokio"]


def test_diamond_conflict_detection():
    """Verify detection of diamond dependency version collisions."""
    graph = DependencyGraph()

    # Add package with multiple versions
    graph.add_node(
        PackageNode(
            name="common-util",
            display_name="common-util",
            version="1.0.0",
            ecosystem="npm",
            lockfile_path="package-lock.json",
        )
    )
    graph.add_node(
        PackageNode(
            name="common-util",
            display_name="common-util",
            version="2.0.0",
            ecosystem="npm",
            lockfile_path="package-lock.json",
        )
    )

    graph.add_edge("lib-alpha", "common-util")
    graph.add_edge("lib-beta", "common-util")

    conflicts = graph.detect_conflicts()
    assert len(conflicts) == 1
    assert conflicts[0].package_name == "common-util"
    assert conflicts[0].versions == ["1.0.0", "2.0.0"]


def test_circular_dependency_graceful_termination():
    """Verify circular dependencies terminate cleanly in resolve_why."""
    graph = DependencyGraph()
    graph.add_node(
        PackageNode(
            name="pkg-a",
            display_name="pkg-a",
            version="1.0.0",
            ecosystem="PyPI",
            lockfile_path="uv.lock",
            is_direct=True,
        )
    )
    graph.add_node(
        PackageNode(
            name="pkg-b",
            display_name="pkg-b",
            version="1.0.0",
            ecosystem="PyPI",
            lockfile_path="uv.lock",
        )
    )
    graph.mark_direct_roots({"pkg-a": "pyproject.toml"})

    # Circular loop
    graph.add_edge("pkg-a", "pkg-b")
    graph.add_edge("pkg-b", "pkg-a")

    chains = graph.resolve_why("pkg-b")
    assert len(chains) == 1
    assert chains[0].path == ["pkg-a", "pkg-b"]


def test_ascii_chain_rendering():
    """Verify formatting of ASCII causation trees."""
    graph = DependencyGraph()
    graph.add_node(
        PackageNode(
            name="httpx",
            display_name="httpx",
            version="0.27.0",
            ecosystem="PyPI",
            lockfile_path="uv.lock",
            is_direct=True,
            direct_manifest="pyproject.toml",
        )
    )
    graph.add_node(
        PackageNode(
            name="certifi",
            display_name="certifi",
            version="2024.2.2",
            ecosystem="PyPI",
            lockfile_path="uv.lock",
        )
    )
    graph.mark_direct_roots({"httpx": "pyproject.toml"})
    graph.add_edge("httpx", "certifi")

    chains = graph.resolve_why("certifi")
    rendered = graph.render_ascii_chains("certifi", chains)
    assert "Transitive Causation Tree for 'certifi'" in rendered
    assert "[DIRECT: pyproject.toml]" in rendered
    assert "[TARGET]" in rendered
    assert "httpx@0.27.0" in rendered


def test_cli_deps_commands(tmp_path: Path, capsys):
    """Verify CLI subcommands `vibetrace deps why`, `tree`, and `conflicts`."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "test-pkg"
version = "0.1.0"
dependencies = ["requests>=2.31.0"]
""",
        encoding="utf-8",
    )

    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text(
        """
version = 1

[[package]]
name = "requests"
version = "2.31.0"
dependencies = [{ name = "urllib3" }]

[[package]]
name = "urllib3"
version = "2.2.0"
""",
        encoding="utf-8",
    )

    # 1. why command
    args_why = argparse.Namespace(path=str(tmp_path), package="urllib3", json=True)
    res_why = _cmd_deps_why(args_why)
    assert res_why == 0
    out_why, _ = capsys.readouterr()
    data_why = json.loads(out_why)
    assert data_why["target"] == "urllib3"
    assert data_why["found"] is True

    # 2. tree command
    args_tree = argparse.Namespace(path=str(tmp_path), target=None, depth=3, json=False)
    res_tree = _cmd_deps_tree(args_tree)
    assert res_tree == 0
    out_tree, _ = capsys.readouterr()
    assert "Project Dependency Tree" in out_tree
    assert "requests" in out_tree

    # 3. conflicts command (no conflicts)
    args_conflicts = argparse.Namespace(path=str(tmp_path), json=False)
    res_conf = _cmd_deps_conflicts(args_conflicts)
    assert res_conf == 0
    out_conf, _ = capsys.readouterr()
    assert "No diamond version conflicts detected" in out_conf


def test_cli_parser_registration():
    """Verify ArgumentParser correctly routes `deps` flags."""
    parser = build_parser()
    args = parser.parse_args(["deps", "why", "certifi", "--json"])
    assert args.deps_action == "why"
    assert args.package == "certifi"
    assert args.json is True

    args_tree = parser.parse_args(["deps", "tree", "--target", "idna", "--depth", "5"])
    assert args_tree.deps_action == "tree"
    assert args_tree.target == "idna"
    assert args_tree.depth == 5


def test_security_audit_transitive_root_cause_enrichment(tmp_path: Path, monkeypatch):
    """Verify that SecurityService.audit_project decorates transitive CVEs with causation paths."""
    from vibetrace.indexer.db import IndexerDB
    from vibetrace.security.models import FindingCategory, SecurityFinding, Severity
    from vibetrace.security.service import SecurityService

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        """
[project]
name = "vuln-app"
version = "0.1.0"
dependencies = ["requests>=2.31.0"]
""",
        encoding="utf-8",
    )

    uv_lock = tmp_path / "uv.lock"
    uv_lock.write_text(
        """
version = 1

[[package]]
name = "requests"
version = "2.31.0"
dependencies = [{ name = "urllib3" }]

[[package]]
name = "urllib3"
version = "1.26.5"
""",
        encoding="utf-8",
    )

    db = IndexerDB(str(tmp_path / "sec_test.db"))

    # Mock OsvClient to return a synthetic vulnerability on transitive urllib3
    mock_cve = SecurityFinding(
        id="SEC-CVE-2023-1234-urllib3-1.26.5",
        rule_id="pypi:CVE-2023-1234",
        cwe_id="CWE-1395",
        category=FindingCategory.PACKAGE_CVE,
        severity=Severity.HIGH,
        title="Vulnerable Package: urllib3@1.26.5",
        message="Request header CRLF injection",
        file_path="uv.lock",
        symbol_name="urllib3",
        code_hash="cve:pypi:urllib3:1.26.5",
        remediation_guidance="Upgrade dependency `urllib3` to version 1.26.18.",
    )

    def mock_query_vulns(packages, db, force_update=False):
        return [mock_cve]

    monkeypatch.setattr(
        "vibetrace.security.osv_client.OsvClient.query_vulnerabilities",
        mock_query_vulns,
    )

    report = SecurityService.audit_project(db, tmp_path, check_dependencies=True)
    assert report.total_findings >= 1

    finding = next(f for f in report.findings if f.symbol_name == "urllib3")
    assert finding.remediation_guidance is not None
    assert "**Transitive Root Cause:** `requests -> urllib3`" in finding.remediation_guidance
    assert "(via direct parent `requests`)" in finding.remediation_guidance
