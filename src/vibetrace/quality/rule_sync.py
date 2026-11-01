"""Self-updating Quality Rule Sync Engine with SQLite TTL caching and bundled fallback.

Syncs clean-code quality rules, Sonar RSPEC definitions, and thresholds from
remote manifests or local bundled resources.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from vibetrace.indexer.db import IndexerDB
from vibetrace.quality.models import QualityCategory, QualitySeverity
from vibetrace.quality.rules import (
    QUALITY_RULES_CATALOG,
    QualityRuleSpec,
    register_ruff_rule,
    register_rule_alias,
)

logger = logging.getLogger(__name__)

DEFAULT_QUALITY_RULES_TTL_DAYS = 7
DEFAULT_QUALITY_RULES_FEED_URL = (
    "https://raw.githubusercontent.com/serguei9090/VibeTrace/main/"
    "src/vibetrace/resources/quality_rules_catalog.json"
)

BUNDLED_CATALOG_PATH = Path(__file__).parent.parent / "resources" / "quality_rules_catalog.json"

_SEVERITY_MAP: dict[str, QualitySeverity] = {
    "BLOCKER": QualitySeverity.HIGH,
    "CRITICAL": QualitySeverity.HIGH,
    "MAJOR": QualitySeverity.MEDIUM,
    "MINOR": QualitySeverity.LOW,
    "INFO": QualitySeverity.LOW,
}

_CATEGORY_MAP: dict[str, QualityCategory] = {
    "BUG": QualityCategory.RELIABILITY,
    "CODE_SMELL": QualityCategory.CODE_SMELL,
    "VULNERABILITY": QualityCategory.RELIABILITY,
    "SECURITY_HOTSPOT": QualityCategory.RELIABILITY,
}


@dataclass
class QualityCatalogSyncResult:
    """Statistics summarizing a quality rules catalog sync run."""

    rules_loaded: int
    source: str  # "cache", "network", "bundled", "builtin"
    cached_at: int
    languages: list[str]


class QualityRuleCatalogManager:
    """Manages downloading, local caching, and loading of the multi-language quality catalog."""

    @classmethod
    def _init_cache_table(cls, db: IndexerDB) -> None:
        """Ensures the quality_rules_cache table exists in SQLite."""
        with db._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quality_rules_cache (
                    feed_url TEXT PRIMARY KEY,
                    manifest_json TEXT NOT NULL,
                    rules_count INTEGER NOT NULL,
                    cached_at INTEGER NOT NULL
                )
                """
            )

    @classmethod
    def _fetch_remote_manifest(
        cls, feed_url: str, timeout_seconds: int = 5
    ) -> dict[str, Any] | None:
        """Fetches remote JSON manifest over HTTP with conservative timeout."""
        try:
            req = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "VibeTrace-Quality-Sync/1.0", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout_seconds) as resp:
                if resp.status == 200:
                    raw = resp.read().decode("utf-8")
                    return json.loads(raw)
        except Exception as e:
            logger.debug("Failed to fetch quality rules from %s: %s", feed_url, e)
        return None

    @classmethod
    def _load_bundled_manifest(cls) -> dict[str, Any] | None:
        """Loads the pre-compiled bundled catalog if present on disk."""
        if BUNDLED_CATALOG_PATH.is_file():
            try:
                raw = BUNDLED_CATALOG_PATH.read_text(encoding="utf-8")
                return json.loads(raw)
            except Exception as e:
                logger.warning("Failed to parse bundled rules catalog: %s", e)
        return None

    @classmethod
    def _check_sqlite_cache(
        cls, db: IndexerDB, feed_url: str, now: int, ttl_seconds: int
    ) -> QualityCatalogSyncResult | None:
        try:
            cls._init_cache_table(db)
            with db._get_conn() as conn:
                query = (
                    "SELECT manifest_json, rules_count, cached_at "
                    "FROM quality_rules_cache WHERE feed_url = ?"
                )
                row = conn.execute(query, (feed_url,)).fetchone()
                if not row:
                    return None
                manifest_json, rules_count, cached_at = row
                if (now - cached_at) >= ttl_seconds:
                    return None
                data = json.loads(manifest_json)
                cls._hydrate_catalog(data.get("rules", []), data.get("ruff_rules"))
                return QualityCatalogSyncResult(
                    rules_loaded=rules_count,
                    source="cache",
                    cached_at=cached_at,
                    languages=data.get("languages", []),
                )
        except Exception as e:
            logger.debug("SQLite cache check failed: %s", e)
            return None

    @classmethod
    def _resolve_manifest(
        cls, feed_url: str, force_update: bool
    ) -> tuple[dict[str, Any] | None, str]:
        if force_update:
            manifest = cls._fetch_remote_manifest(feed_url)
            if manifest:
                return manifest, "network"
        manifest = cls._load_bundled_manifest()
        if manifest:
            return manifest, "bundled"
        manifest = cls._fetch_remote_manifest(feed_url)
        if manifest:
            return manifest, "network"
        return None, "builtin"

    @classmethod
    def _save_to_sqlite(
        cls, db: IndexerDB, feed_url: str, manifest: dict[str, Any], count: int, now: int
    ) -> None:
        try:
            cls._init_cache_table(db)
            with db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO quality_rules_cache
                    (feed_url, manifest_json, rules_count, cached_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (feed_url, json.dumps(manifest), count, now),
                )
        except Exception as e:
            logger.debug("Failed saving quality manifest to SQLite: %s", e)

    @classmethod
    def sync_catalog(
        cls,
        db: IndexerDB | None = None,
        feed_url: str = DEFAULT_QUALITY_RULES_FEED_URL,
        force_update: bool = False,
        ttl_days: int = DEFAULT_QUALITY_RULES_TTL_DAYS,
    ) -> QualityCatalogSyncResult:
        """Synchronizes rules from SQLite cache, remote feed, or bundled asset."""
        now = int(time.time())
        ttl_seconds = ttl_days * 86400

        # 1. Check local SQLite cache if DB provided and not force_update
        if db is not None and not force_update:
            cached_res = cls._check_sqlite_cache(db, feed_url, now, ttl_seconds)
            if cached_res:
                return cached_res

        # 2. Resolve manifest from remote or bundled source
        manifest, source = cls._resolve_manifest(feed_url, force_update)

        # 3. If we have a manifest, store in SQLite and hydrate into catalog
        if manifest and "rules" in manifest:
            rules = manifest.get("rules", [])
            count = len(rules)
            cls._hydrate_catalog(rules, manifest.get("ruff_rules"))

            if db is not None:
                cls._save_to_sqlite(db, feed_url, manifest, count, now)

            return QualityCatalogSyncResult(
                rules_loaded=count,
                source=source,
                cached_at=now,
                languages=manifest.get("languages", []),
            )

        # 4. Fallback to native hardcoded catalog
        return QualityCatalogSyncResult(
            rules_loaded=len(QUALITY_RULES_CATALOG),
            source="builtin",
            cached_at=now,
            languages=["py", "css", "ts", "docker"],
        )

    @classmethod
    def _build_rule_spec(cls, r: dict[str, Any], clean_key: str, raw_key: str) -> QualityRuleSpec:
        sev_str = r.get("severity", "MAJOR")
        type_str = r.get("type", "CODE_SMELL")
        name = r.get("name", clean_key)
        desc = r.get("desc", name)
        params = r.get("params") or {}
        thresh: int | None = None
        if "threshold" in params and str(params["threshold"]).isdigit():
            thresh = int(params["threshold"])

        cwes: list[str] = r.get("cwes") or []
        rspec_id = f"RSPEC-{clean_key.lstrip('S')}" if clean_key.startswith("S") else clean_key
        return QualityRuleSpec(
            rule_id=raw_key,
            rspec_id=rspec_id,
            severity=_SEVERITY_MAP.get(sev_str, QualitySeverity.MEDIUM),
            category=_CATEGORY_MAP.get(type_str, QualityCategory.CODE_SMELL),
            title=name,
            explanation=desc,
            non_compliant_example="",
            compliant_example="",
            default_threshold=thresh,
            aliases=r.get("tags", []),
            cwe_ids=cwes,
        )

    @classmethod
    def _hydrate_catalog(
        cls,
        rules: list[dict[str, Any]],
        ruff_rules: dict[str, Any] | None = None,
    ) -> None:
        """Injects remote or bundled rules and Ruff specs into QUALITY_RULES_CATALOG dictionary."""
        for r in rules:
            raw_key = r.get("key", "")
            clean_key = raw_key.split(":")[-1].upper()

            # Preserve native hand-crafted rules that already have specialized Python AST handlers
            if clean_key in QUALITY_RULES_CATALOG:
                continue

            spec = cls._build_rule_spec(r, clean_key, raw_key)
            QUALITY_RULES_CATALOG[clean_key] = spec
            QUALITY_RULES_CATALOG[raw_key] = spec
            register_rule_alias(clean_key, clean_key)
            register_rule_alias(raw_key, clean_key)
            for tag in r.get("tags", []):
                register_rule_alias(tag, clean_key)

        if ruff_rules:
            for code, rdata in ruff_rules.items():
                ruff_code = rdata.get("code", code).upper()
                ruff_spec = QualityRuleSpec(
                    rule_id=ruff_code,
                    rspec_id=f"RUFF-{ruff_code}",
                    severity=QualitySeverity.MEDIUM,
                    category=QualityCategory.MAINTAINABILITY,
                    title=rdata.get("summary") or rdata.get("name", ruff_code),
                    explanation=(
                        f"Ruff {rdata.get('linter', 'linter')} rule {ruff_code}: "
                        f"{rdata.get('summary', '')}"
                    ),
                    non_compliant_example="",
                    compliant_example="",
                    aliases=[rdata["name"]] if rdata.get("name") else [],
                )
                register_ruff_rule(ruff_spec)
