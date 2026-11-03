"""Unit tests for Trivy Security Adapter, container detection, and image audit integration."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.adapters.trivy import TrivyAdapter
from vibetrace.security.models import Severity
from vibetrace.security.service import SecurityService
from vibetrace.skill_installer import initialize_workspace


def test_trivy_adapter_container_detection(tmp_path: Path):
    """Verifies detection of Dockerfile, Compose, and Kubernetes configurations."""
    assert not TrivyAdapter.has_container_configs(tmp_path)

    # 1. Dockerfile
    df = tmp_path / "Dockerfile"
    df.write_text("FROM alpine:3.19\nRUN apk add --no-cache curl\n", encoding="utf-8")
    assert TrivyAdapter.has_container_configs(tmp_path)
    df.unlink()

    # 2. docker-compose.yml
    dc = tmp_path / "docker-compose.yml"
    dc.write_text("services:\n  web:\n    image: nginx:latest\n", encoding="utf-8")
    assert TrivyAdapter.has_container_configs(tmp_path)
    dc.unlink()

    # 3. k8s directory
    k8s = tmp_path / "k8s"
    k8s.mkdir()
    assert TrivyAdapter.has_container_configs(tmp_path)


def test_trivy_adapter_graceful_when_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies that missing Trivy binary gracefully returns empty findings without error."""
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    assert not TrivyAdapter.is_available()

    assert TrivyAdapter.scan_filesystem(tmp_path) == []
    assert TrivyAdapter.scan_image("myapp:latest", tmp_path) == []


def test_trivy_sarif_ingestion(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies parsing and translating Trivy SARIF outputs into VibeTrace SecurityFindings."""
    fake_sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Trivy",
                        "rules": [
                            {
                                "id": "CVE-2024-9999",
                                "shortDescription": {"text": "Vulnerability in libssl3"},
                                "properties": {"cwe": ["CWE-327"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "CVE-2024-9999",
                        "level": "error",
                        "message": {"text": "libssl3 before 3.0.13 has buffer overflow"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "usr/lib/libssl.so.3"},
                                    "region": {"startLine": 1, "endLine": 1},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }

    class MockCompletedProcess:
        def __init__(self, stdout: str):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    monkeypatch.setattr(shutil, "which", lambda cmd: "/usr/local/bin/trivy")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: MockCompletedProcess(json.dumps(fake_sarif)),
    )

    findings = TrivyAdapter.scan_filesystem(tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.rule_id in ("CVE-2024-9999", "trivy:CVE-2024-9999")
    assert f.cwe_id == "CWE-327"
    assert f.severity == Severity.HIGH
    assert f.metadata.get("scanner") == "trivy"
    assert f.metadata.get("trivy_scope") == "fs"


def test_trivy_image_scan_and_service_integration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifies image scanning via Trivy and routing through SecurityService."""
    fake_sarif = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "Trivy",
                        "rules": [
                            {
                                "id": "CVE-2024-1111",
                                "shortDescription": {"text": "Alpine musl libc issue"},
                                "properties": {"cwe": ["CWE-78"]},
                            }
                        ],
                    }
                },
                "results": [
                    {
                        "ruleId": "CVE-2024-1111",
                        "level": "error",
                        "message": {"text": "musl DNS parsing vulnerability"},
                        "locations": [
                            {
                                "physicalLocation": {
                                    "artifactLocation": {"uri": "."},
                                    "region": {"startLine": 1, "endLine": 1},
                                }
                            }
                        ],
                    }
                ],
            }
        ],
    }

    class MockCompletedProcess:
        def __init__(self, stdout: str):
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    monkeypatch.setattr(shutil, "which", lambda cmd: "trivy")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *args, **kwargs: MockCompletedProcess(json.dumps(fake_sarif)),
    )

    ctx = tmp_path / ".vibetrace"
    ctx.mkdir(parents=True, exist_ok=True)
    db = IndexerDB(str(ctx / "vibetrace.db"))

    # Test audit_project with image_tag
    report = SecurityService.audit_project(
        db,
        tmp_path,
        check_dependencies=False,
        image_tag="alpine:3.19",
    )
    trivy_findings = [f for f in report.findings if f.metadata.get("scanner") == "trivy"]
    assert len(trivy_findings) == 1
    tf = trivy_findings[0]
    assert tf.file_path == "image:alpine:3.19"
    assert tf.metadata.get("image_tag") == "alpine:3.19"

    # Test with enable_trivy=False (skipped)
    report_skip = SecurityService.audit_project(
        db,
        tmp_path,
        check_dependencies=False,
        image_tag="alpine:3.19",
        enable_trivy=False,
    )
    trivy_skipped = [f for f in report_skip.findings if f.metadata.get("scanner") == "trivy"]
    assert len(trivy_skipped) == 0


def test_workspace_init_container_detection(tmp_path: Path):
    """Verifies initialize_workspace detects container artifacts and returns metadata."""
    (tmp_path / "Dockerfile").write_text("FROM alpine\n", encoding="utf-8")
    res = initialize_workspace(tmp_path, no_index=True)
    assert res["has_containers"] is True
    assert "trivy_available" in res
