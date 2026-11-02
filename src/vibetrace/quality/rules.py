"""Built-in Quality & Clean Code Rules Catalog with cross-ecosystem alias resolution."""

from __future__ import annotations

from vibetrace.quality.rules_catalog import QUALITY_RULES_CATALOG, QualityRuleSpec

__all__ = [
    "QUALITY_RULES_CATALOG",
    "QualityRuleSpec",
    "get_rule_spec",
    "register_ruff_rule",
    "register_rule_alias",
    "resolve_canonical_rule_id",
]

_ALIAS_INDEX: dict[str, str] = {}
RUFF_RULES_CATALOG: dict[str, QualityRuleSpec] = {}


def register_rule_alias(alias: str, canonical_id: str, overwrite: bool = False) -> None:
    """Dynamically associates an external vendor alias with a canonical rule ID."""
    key = alias.strip().upper()
    if overwrite or key not in _ALIAS_INDEX:
        _ALIAS_INDEX[key] = canonical_id


def register_ruff_rule(rule_spec: QualityRuleSpec) -> None:
    """Registers a synthesized or imported Ruff rule specification."""
    clean_code = rule_spec.rule_id.strip().upper()
    RUFF_RULES_CATALOG[clean_code] = rule_spec
    register_rule_alias(clean_code, clean_code, overwrite=False)
    for al in rule_spec.aliases:
        register_rule_alias(al, clean_code, overwrite=False)


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
    """Retrieves the rule specification using canonical ID, RSPEC ID, or vendor alias."""
    canonical = resolve_canonical_rule_id(rule_or_alias)
    if canonical in QUALITY_RULES_CATALOG:
        return QUALITY_RULES_CATALOG[canonical]
    if canonical in RUFF_RULES_CATALOG:
        return RUFF_RULES_CATALOG[canonical]
    clean = rule_or_alias.split(":")[-1].strip()
    return (
        QUALITY_RULES_CATALOG.get(clean)
        or QUALITY_RULES_CATALOG.get(rule_or_alias)
        or RUFF_RULES_CATALOG.get(clean.upper())
    )
