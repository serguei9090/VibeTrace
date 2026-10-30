"""Google OSV.dev REST client with local SQLite 24h caching and offline fallback."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.lockfile_parser import DependencyPackage
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

OSV_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
CACHE_TTL_SECONDS = 86400  # 24 Hours


class OsvClient:
    """Queries OSV.dev batch API for package CVEs with local SQLite caching."""

    @classmethod
    def _init_cache_table(cls, db: IndexerDB) -> None:
        """Ensures the osv_cve_cache table exists in SQLite."""
        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS osv_cve_cache (
                    ecosystem TEXT NOT NULL,
                    package_name TEXT NOT NULL,
                    version TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    cached_at INTEGER NOT NULL,
                    PRIMARY KEY (ecosystem, package_name, version)
                )
                """
            )

    @classmethod
    def query_vulnerabilities(
        cls,
        packages: list[DependencyPackage],
        db: IndexerDB,
        force_update: bool = False,
    ) -> list[SecurityFinding]:
        """Queries OSV for vulnerabilities, pulling from cache when valid (< 24h)."""
        if not packages:
            return []

        cls._init_cache_table(db)
        now_ts = int(time.time())

        findings: list[SecurityFinding] = []
        needed_queries: list[DependencyPackage] = []
        cached_results: dict[tuple[str, str, str], list[dict[str, Any]]] = {}

        # 1. Check local SQLite cache
        with db._get_conn() as conn:
            for pkg in packages:
                if not force_update:
                    row = conn.execute(
                        """
                        SELECT response_json, cached_at FROM osv_cve_cache
                        WHERE ecosystem = ? AND package_name = ? AND version = ?
                        """,
                        (pkg.ecosystem, pkg.name, pkg.version),
                    ).fetchone()
                    if row and (now_ts - row["cached_at"]) < CACHE_TTL_SECONDS:
                        try:
                            vulns = json.loads(row["response_json"])
                            cached_results[(pkg.ecosystem, pkg.name, pkg.version)] = vulns
                            continue
                        except Exception:
                            pass
                needed_queries.append(pkg)

        # 2. Fetch missing from OSV in batches of 500
        if needed_queries:
            newly_fetched = cls._fetch_from_osv(needed_queries)
            # Save newly fetched into cache
            if newly_fetched:
                with db._get_conn() as conn:
                    for (eco, name, ver), vulns in newly_fetched.items():
                        conn.execute(
                            """
                            INSERT OR REPLACE INTO osv_cve_cache
                            (ecosystem, package_name, version, response_json, cached_at)
                            VALUES (?, ?, ?, ?, ?)
                            """,
                            (eco, name, ver, json.dumps(vulns), now_ts),
                        )
                        cached_results[(eco, name, ver)] = vulns

        # 3. Transform vulnerability payloads into SecurityFinding objects
        for pkg in packages:
            vulns = cached_results.get((pkg.ecosystem, pkg.name, pkg.version), [])
            for v in vulns:
                findings.append(cls._osv_to_finding(pkg, v))

        return findings

    @classmethod
    def _fetch_from_osv(
        cls, packages: list[DependencyPackage]
    ) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        """Dispatches POST requests to OSV querybatch endpoint."""
        results: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        batch_size = 500

        for i in range(0, len(packages), batch_size):
            chunk = packages[i : i + batch_size]
            payload = {
                "queries": [
                    {
                        "package": {"name": p.name, "ecosystem": p.ecosystem},
                        "version": p.version,
                    }
                    for p in chunk
                ]
            }

            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    OSV_QUERYBATCH_URL,
                    data=data,
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "VibeTrace-Security/1.0",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10.0) as resp:
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    batch_res = resp_json.get("results", [])
                    for idx, res in enumerate(batch_res):
                        pkg = chunk[idx]
                        vulns = res.get("vulns", [])
                        results[(pkg.ecosystem, pkg.name, pkg.version)] = vulns
            except (urllib.error.URLError, TimeoutError, OSError):
                # Silent offline fallback: do not crash audit if network is unavailable
                pass

        return results

    @classmethod
    def _osv_to_finding(cls, pkg: DependencyPackage, vuln: dict[str, Any]) -> SecurityFinding:
        """Maps an OSV vulnerability record to a VibeTrace SecurityFinding."""
        cve_id = vuln.get("id", "UNKNOWN-CVE")
        summary = (
            vuln.get("summary") or vuln.get("details", "")[:120] or f"Vulnerability in {pkg.name}"
        )

        # Resolve severity
        severity = Severity.HIGH
        database_specific = vuln.get("database_specific", {})
        sev_str = database_specific.get("severity", "").upper()
        if sev_str in Severity.__members__:
            severity = Severity[sev_str]
        elif "CRITICAL" in summary.upper():
            severity = Severity.CRITICAL
        elif "LOW" in summary.upper():
            severity = Severity.LOW
        elif "MODERATE" in summary.upper() or "MEDIUM" in summary.upper():
            severity = Severity.MEDIUM

        finding_id = f"SEC-{cve_id}-{pkg.name}-{pkg.version}"
        code_hash = f"cve:{pkg.ecosystem}:{pkg.name}:{pkg.version}"

        # Find fixed version if available
        fixed_version = "latest"
        for affected in vuln.get("affected", []):
            for r in affected.get("ranges", []):
                for ev in r.get("events", []):
                    if "fixed" in ev:
                        fixed_version = ev["fixed"]
                        break

        remediation = (
            f"Upgrade dependency `{pkg.name}` to version `{fixed_version}` "
            f"or higher in `{pkg.lockfile_path}`."
        )

        return SecurityFinding(
            id=finding_id,
            rule_id=f"{pkg.ecosystem.lower()}:{cve_id}",
            cwe_id="CWE-1395",  # Dependency with known vulnerabilities
            category=FindingCategory.PACKAGE_CVE,
            severity=severity,
            title=f"Vulnerable Package: {pkg.name}@{pkg.version} ({cve_id})",
            message=summary,
            file_path=pkg.lockfile_path,
            start_line=1,
            end_line=1,
            symbol_name=pkg.name,
            code_hash=code_hash,
            non_compliant_snippet=f'{pkg.name} = "{pkg.version}"',
            compliant_snippet=f'{pkg.name} >= "{fixed_version}"',
            remediation_guidance=remediation,
            metadata={
                "cve_id": cve_id,
                "ecosystem": pkg.ecosystem,
                "aliases": vuln.get("aliases", []),
            },
        )
