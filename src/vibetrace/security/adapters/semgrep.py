"""Semgrep SAST Adapter for deep data-flow and taint analysis via SARIF 2.1.0 ingestion."""

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


class SemgrepAdapter:
    """Invokes Semgrep CLI when available on $PATH and feeds SARIF output into SarifIngestEngine."""

    @classmethod
    def is_available(cls) -> bool:
        """Returns True if the `semgrep` executable is available on the system PATH."""
        return shutil.which("semgrep") is not None

    @classmethod
    def scan_project(
        cls,
        project_root: Path,
        db: IndexerDB | None = None,
        target_files: list[str] | None = None,
        config: str = "auto",
        timeout_seconds: int = 90,
    ) -> list[SecurityFinding]:
        """Runs Semgrep in quiet SARIF mode and enriches results with AST reachability."""
        if not cls.is_available():
            logger.debug("Semgrep is not available on PATH; skipping external SAST scan.")
            return []

        cmd = [
            "semgrep",
            "scan",
            "--sarif",
            "--quiet",
            "--metrics=off",
            "--config",
            config,
        ]
        if target_files:
            cmd.extend(target_files[:100])
        else:
            cmd.append(".")

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(project_root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
            raw_output = (proc.stdout or "").strip()
            if not raw_output:
                if proc.stderr:
                    logger.debug("Semgrep stderr output: %s", proc.stderr)
                return []

            data: dict[str, Any] = json.loads(raw_output)
            findings = SarifIngestEngine.parse_sarif_dict(data, project_root, db=db)
            for f in findings:
                f.metadata.setdefault("scanner", "semgrep")
            return findings
        except subprocess.TimeoutExpired:
            logger.warning("Semgrep scan timed out after %d seconds.", timeout_seconds)
            return []
        except (OSError, json.JSONDecodeError) as err:
            logger.debug("Failed to execute or parse Semgrep output: %s", err)
            return []
