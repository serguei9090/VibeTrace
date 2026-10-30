"""Google OSV.dev REST client with local SQLite 24h caching, hydration, and alias deduplication."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.security.lockfile_parser import DependencyPackage
from vibetrace.security.models import FindingCategory, SecurityFinding, Severity

OSV_QUERYBATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_QUERY_URL = "https://api.osv.dev/v1/query"
CACHE_TTL_SECONDS = 86400  # 24 Hours


class OsvClient:
    """Queries OSV.dev batch API for package CVEs with hydration, deduplication, and caching."""

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
    def _is_unhydrated_stub_list(cls, vulns: list[dict[str, Any]]) -> bool:
        """Returns True if cached vulns are raw querybatch stubs lacking summary/details."""
        if not vulns:
            return False
        return all("summary" not in v and "details" not in v and "affected" not in v for v in vulns)

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
                            if not cls._is_unhydrated_stub_list(vulns):
                                cached_results[(pkg.ecosystem, pkg.name, pkg.version)] = vulns
                                continue
                        except Exception:
                            pass
                needed_queries.append(pkg)

        # 2. Fetch missing from OSV in batches of 500 and hydrate affected packages
        if needed_queries:
            newly_fetched = cls._fetch_from_osv(needed_queries)
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

        # 3. Transform deduplicated vulnerability payloads into SecurityFinding objects
        for pkg in packages:
            raw_vulns = cached_results.get((pkg.ecosystem, pkg.name, pkg.version), [])
            for v in cls._deduplicate_vulns(raw_vulns):
                findings.append(cls._osv_to_finding(pkg, v))

        return findings

    @classmethod
    def _fetch_from_osv(
        cls, packages: list[DependencyPackage]
    ) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
        """Dispatches POST requests to OSV querybatch and hydrates affected packages."""
        results: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        batch_size = 500
        to_hydrate: list[DependencyPackage] = []

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
                with urllib.request.urlopen(req, timeout=10.0) as resp:  # nosec
                    resp_json = json.loads(resp.read().decode("utf-8"))
                    batch_res = resp_json.get("results", [])
                    for idx, res in enumerate(batch_res):
                        pkg = chunk[idx]
                        vulns = res.get("vulns", [])
                        results[(pkg.ecosystem, pkg.name, pkg.version)] = vulns
                        if cls._is_unhydrated_stub_list(vulns):
                            to_hydrate.append(pkg)
            except (urllib.error.URLError, TimeoutError, OSError):
                # Silent offline fallback: do not crash audit if network is unavailable
                pass

        if to_hydrate:
            with ThreadPoolExecutor(max_workers=min(8, len(to_hydrate))) as pool:
                hydrated_list = list(pool.map(cls._hydrate_package_vulns, to_hydrate))
            for pkg, full_vulns in zip(to_hydrate, hydrated_list, strict=False):
                if full_vulns:
                    results[(pkg.ecosystem, pkg.name, pkg.version)] = full_vulns

        return results

    @classmethod
    def _hydrate_package_vulns(cls, pkg: DependencyPackage) -> list[dict[str, Any]]:
        """Fetches full vulnerability objects for a single affected package via /v1/query."""
        payload = {
            "package": {"name": pkg.name, "ecosystem": pkg.ecosystem},
            "version": pkg.version,
        }
        try:
            req = urllib.request.Request(
                OSV_QUERY_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "VibeTrace-Security/1.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10.0) as resp:  # nosec
                data = json.loads(resp.read().decode("utf-8"))
                return data.get("vulns", [])
        except Exception:
            return []

    @classmethod
    def _deduplicate_vulns(cls, vulns: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicates GHSA/PYSEC/CVE alias records for the same vulnerability."""
        if len(vulns) <= 1:
            return vulns

        # Sort so richer records (with summary + database_specific severity + GHSA/CVE) come first
        def _rank(v: dict[str, Any]) -> tuple[int, int, int]:
            has_summary = 1 if v.get("summary") else 0
            has_sev = 1 if v.get("database_specific", {}).get("severity") else 0
            vid = str(v.get("id", ""))
            is_ghsa_or_cve = 1 if vid.startswith(("GHSA-", "CVE-")) else 0
            return (has_summary, has_sev, is_ghsa_or_cve)

        sorted_vulns = sorted(vulns, key=_rank, reverse=True)
        seen_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []

        for v in sorted_vulns:
            vid = str(v.get("id", ""))
            aliases = [str(a) for a in (v.get("aliases") or []) if a]
            all_keys = {vid, *aliases}
            if all_keys & seen_ids:
                continue
            seen_ids.update(all_keys)
            deduped.append(v)

        return deduped

    @classmethod
    def _osv_to_finding(cls, pkg: DependencyPackage, vuln: dict[str, Any]) -> SecurityFinding:
        """Maps an OSV vulnerability record to a VibeTrace SecurityFinding."""
        cve_id = vuln.get("id", "UNKNOWN-CVE")
        summary = (
            vuln.get("summary")
            or (vuln.get("details") or "").splitlines()[0][:140]
            or f"Vulnerability in {pkg.name}"
        )

        # Resolve severity (OSV/GHSA uses CRITICAL, HIGH, MODERATE, LOW)
        severity = Severity.HIGH
        database_specific = vuln.get("database_specific") or {}
        sev_str = str(database_specific.get("severity", "")).upper()
        if sev_str == "MODERATE":
            severity = Severity.MEDIUM
        elif sev_str in Severity.__members__:
            severity = Severity[sev_str]
        elif "CRITICAL" in summary.upper():
            severity = Severity.CRITICAL
        elif "LOW" in summary.upper():
            severity = Severity.LOW
        elif "MODERATE" in summary.upper() or "MEDIUM" in summary.upper():
            severity = Severity.MEDIUM

        finding_id = f"SEC-{cve_id}-{pkg.name}-{pkg.version}"
        code_hash = f"cve:{pkg.ecosystem}:{pkg.name}:{pkg.version}"

        # Find fixed version for this package if available
        fixed_version = "latest"
        for affected in vuln.get("affected", []):
            aff_pkg = affected.get("package") or {}
            if aff_pkg.get("name") and aff_pkg.get("name", "").lower() != pkg.name.lower():
                continue
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
                "aliases": vuln.get("aliases") or [],
                "fixed_version": fixed_version,
            },
        )
