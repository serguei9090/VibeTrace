"""Trivy Security Adapter for Container Images, Base OS Packages, and IaC Scanning."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.models import SecurityFinding
from vibetrace.security.sarif_ingest import SarifIngestEngine

logger = logging.getLogger(__name__)

_CONTAINER_FILES = (
    "Dockerfile",
    "Dockerfile.dev",
    "Dockerfile.prod",
    "docker-compose.yml",
    "docker-compose.yaml",
    "compose.yml",
    "compose.yaml",
)
_K8S_DIRS = ("k8s", "kubernetes", "helm", "deploy")


class TrivyAdapter:
    """Invokes Aqua Trivy CLI when available on $PATH to scan containers and OS packages."""

    @classmethod
    def is_available(cls) -> bool:
        """Returns True if the `trivy` executable is available on the system PATH."""
        return shutil.which("trivy") is not None

    @classmethod
    def has_container_configs(cls, project_root: Path) -> bool:
        """Detects whether the workspace contains Dockerfile, Compose, or Kubernetes manifests."""
        root = Path(project_root)
        for cf in _CONTAINER_FILES:
            if (root / cf).is_file():
                return True
        for kd in _K8S_DIRS:
            if (root / kd).is_dir():
                return True
        try:
            if any(root.glob("Dockerfile*")) or any(root.glob("*.k8s.y*ml")):
                return True
        except OSError:
            pass
        return False

    @classmethod
    def scan_filesystem(
        cls,
        project_root: Path,
        db: IndexerDB | None = None,
        timeout_seconds: int = 60,
    ) -> list[SecurityFinding]:
        """Scans workspace filesystem for container, IaC, and dependency misconfigurations."""
        if not cls.is_available():
            logger.debug("Trivy is not available on PATH; skipping filesystem scan.")
            return []

        cmd = [
            "trivy",
            "fs",
            "--format",
            "sarif",
            "--scanners",
            "vuln,misconfig,secret",
            "-q",
            ".",
        ]
        return cls._run_and_parse(cmd, project_root, db=db, timeout=timeout_seconds, scope="fs")

    @classmethod
    def scan_image(
        cls,
        image_tag: str,
        project_root: Path,
        db: IndexerDB | None = None,
        timeout_seconds: int = 120,
    ) -> list[SecurityFinding]:
        """Scans a built local or remote container image tag for OS-level library CVEs."""
        if not cls.is_available():
            logger.warning(
                "Trivy is not available on PATH. Cannot scan container image '%s'.", image_tag
            )
            return []

        cmd = [
            "trivy",
            "image",
            "--format",
            "sarif",
            "-q",
            image_tag,
        ]
        findings = cls._run_and_parse(
            cmd, project_root, db=db, timeout=timeout_seconds, scope="image"
        )
        for f in findings:
            f.metadata["image_tag"] = image_tag
            if not f.file_path or f.file_path == ".":
                f.file_path = f"image:{image_tag}"
        return findings

    @classmethod
    def _run_and_parse(
        cls,
        cmd: list[str],
        project_root: Path,
        db: IndexerDB | None,
        timeout: int,
        scope: str,
    ) -> list[SecurityFinding]:
        """Executes Trivy subprocess and translates SARIF output to SecurityFindings."""
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            raw_output = proc.stdout.strip()
            if not raw_output:
                if proc.stderr:
                    logger.debug("Trivy stderr output: %s", proc.stderr)
                return []

            data: dict[str, Any] = json.loads(raw_output)
            findings = SarifIngestEngine.parse_sarif_dict(data, project_root, db=db)
            for f in findings:
                f.metadata.setdefault("scanner", "trivy")
                f.metadata.setdefault("trivy_scope", scope)
            return findings
        except subprocess.TimeoutExpired:
            logger.warning("Trivy scan timed out after %d seconds.", timeout)
            return []
        except (OSError, json.JSONDecodeError) as err:
            logger.debug("Failed to execute or parse Trivy output: %s", err)
            return []
