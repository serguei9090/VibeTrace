"""Built-in Quality & Clean Code Rules Catalog with cross-ecosystem alias resolution."""

from __future__ import annotations

from vibetrace.quality.rules_catalog import QUALITY_RULES_CATALOG, QualityRuleSpec

__all__ = [
    "QUALITY_RULES_CATALOG",
    "QualityRuleSpec",
    "get_rule_spec",
    "register_rule_alias",
    "resolve_canonical_rule_id",
]

_ALIAS_INDEX: dict[str, str] = {}


def register_rule_alias(alias: str, canonical_id: str, overwrite: bool = False) -> None:
    """Associates an external vendor alias with a canonical native rule ID."""
    key = alias.strip().upper()
    if overwrite or key not in _ALIAS_INDEX:
        _ALIAS_INDEX[key] = canonical_id


for _cid, _spec in QUALITY_RULES_CATALOG.items():
    _ALIAS_INDEX[_cid.upper()] = _cid
    _ALIAS_INDEX[_spec.rspec_id.upper()] = _cid
    for _al in _spec.aliases:
        _ALIAS_INDEX[_al.upper()] = _cid


def resolve_canonical_rule_id(rule_or_alias: str) -> str:
    """Maps any vendor alias (e.g. 'B006', 'SIM102', 'Google:DeadCode') to canonical rule ID."""
    clean = rule_or_alias.split(":")[-1].strip().upper()
    if clean in _ALIAS_INDEX:
        return _ALIAS_INDEX[clean]
    raw_upper = rule_or_alias.strip().upper()
    if raw_upper in _ALIAS_INDEX:
        return _ALIAS_INDEX[raw_upper]
    return rule_or_alias


def get_rule_spec(rule_or_alias: str) -> QualityRuleSpec | None:
    """Retrieves the native rule specification using canonical ID, RSPEC ID, or vendor alias."""
    canonical = resolve_canonical_rule_id(rule_or_alias)
    if canonical in QUALITY_RULES_CATALOG:
        return QUALITY_RULES_CATALOG[canonical]
    clean = rule_or_alias.split(":")[-1].strip()
    return QUALITY_RULES_CATALOG.get(clean) or QUALITY_RULES_CATALOG.get(rule_or_alias)
