"""MITRE CWE & OWASP Top 10 Taxonomy Manager for VibeTrace.

Provides canonical lookups, descriptions, OWASP mappings, and badges for both
Security and Quality findings.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CWE_CATALOG_PATH = Path(__file__).parent / "resources" / "cwe_catalog.json"
_CWE_REGEX = re.compile(r"(?i)\bcwe[-_]?(\d+)\b")

_CWE_CACHE: dict[str, dict[str, Any]] | None = None


def normalize_cwe_id(raw_cwe: str) -> str:
    """Normalizes any variation ('89', 'cwe-89', 'CWE_89') to canonical 'CWE-89'."""
    clean = raw_cwe.strip().upper().replace("_", "-")
    if clean.isdigit():
        return f"CWE-{clean}"
    if clean.startswith("CWE-"):
        return clean
    m = _CWE_REGEX.search(clean)
    if m:
        return f"CWE-{m.group(1)}"
    return clean


class CweTaxonomyManager:
    """Provides taxonomy definitions, OWASP Top 10 mappings, and badge generation."""

    @classmethod
    def _load_catalog(cls) -> dict[str, dict[str, Any]]:
        global _CWE_CACHE
        if _CWE_CACHE is not None:
            return _CWE_CACHE

        catalog: dict[str, dict[str, Any]] = {}
        if CWE_CATALOG_PATH.is_file():
            try:
                raw_text = CWE_CATALOG_PATH.read_text(encoding="utf-8")
                loaded = json.loads(raw_text)
                if isinstance(loaded, dict):
                    catalog = loaded
            except Exception as e:
                logger.warning("Failed to load CWE catalog from %s: %s", CWE_CATALOG_PATH, e)

        _CWE_CACHE = catalog
        return _CWE_CACHE

    @classmethod
    def get_cwe(cls, cwe_id: str) -> dict[str, Any] | None:
        """Retrieves the full dictionary entry for a given CWE ID."""
        catalog = cls._load_catalog()
        normalized = normalize_cwe_id(cwe_id)
        return catalog.get(normalized)

    @classmethod
    def get_cwe_name(cls, cwe_id: str) -> str | None:
        """Returns the human-readable title/name of the CWE."""
        entry = cls.get_cwe(cwe_id)
        return entry.get("name") if entry else None

    @classmethod
    def get_cwe_description(cls, cwe_id: str) -> str | None:
        """Returns the summary description of the CWE."""
        entry = cls.get_cwe(cwe_id)
        return entry.get("description") if entry else None

    @classmethod
    def get_owasp_category(cls, cwe_id: str) -> str | None:
        """Returns the OWASP Top 10 category (e.g. 'A03:2021-Injection') if mapped."""
        entry = cls.get_cwe(cwe_id)
        return entry.get("owasp") if entry else None

    @classmethod
    def get_owasp_short(cls, cwe_id: str) -> str | None:
        """Returns the concise OWASP category identifier (e.g. 'A03:2021')."""
        owasp = cls.get_owasp_category(cwe_id)
        if owasp:
            return owasp.split("-")[0].strip()
        return None

    @classmethod
    def get_badge(cls, cwe_id: str) -> str:
        """Generates a standardized display badge (e.g. '[CWE-89 | OWASP A03:2021]')."""
        norm = normalize_cwe_id(cwe_id)
        owasp_short = cls.get_owasp_short(norm)
        if owasp_short:
            return f"[{norm} | OWASP {owasp_short}]"
        return f"[{norm}]"

    @classmethod
    def extract_cwe_ids(cls, text_or_tags: str | list[str]) -> list[str]:
        """Extracts all referenced CWE IDs from a string or list of tag strings."""
        found: list[str] = []
        if isinstance(text_or_tags, list):
            target_str = " ".join(text_or_tags)
        else:
            target_str = str(text_or_tags)

        for match in _CWE_REGEX.finditer(target_str):
            cid = f"CWE-{match.group(1)}"
            if cid not in found:
                found.append(cid)
        return found

    @classmethod
    def enrich_finding(cls, finding: Any) -> None:
        """Enriches a SecurityFinding or QualityFinding with canonical CWE & OWASP metadata."""
        # 1. Resolve primary CWE
        primary_cwe = getattr(finding, "cwe_id", None)
        if primary_cwe:
            primary_cwe = normalize_cwe_id(primary_cwe)
            finding.cwe_id = primary_cwe

        # 2. Extract and merge CWE list
        cwe_ids: list[str] = list(getattr(finding, "cwe_ids", []))
        if primary_cwe and primary_cwe not in cwe_ids:
            cwe_ids.insert(0, primary_cwe)

        # Also search in title, message, rule_id, or explanation if cwe_ids is empty
        if not cwe_ids:
            searchable = f"{getattr(finding, 'rule_id', '')} {getattr(finding, 'title', '')}"
            extracted = cls.extract_cwe_ids(searchable)
            for eid in extracted:
                if eid not in cwe_ids:
                    cwe_ids.append(eid)
            if not primary_cwe and cwe_ids:
                finding.cwe_id = cwe_ids[0]
                primary_cwe = cwe_ids[0]

        finding.cwe_ids = cwe_ids

        # 3. Resolve OWASP Top 10 category
        if primary_cwe and not getattr(finding, "owasp_category", None):
            owasp = cls.get_owasp_category(primary_cwe)
            if owasp:
                finding.owasp_category = owasp
