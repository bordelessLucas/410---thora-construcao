"""Perfis de documento orçamentário (registry + adapters)."""

from app.domain.profiles.base import DocumentProfile, EconomicSource, ProfileMatch, TableKind
from app.domain.profiles.registry import (
    classify_table_kind_via_profile,
    get_profile,
    list_profiles,
    match_profile,
    parse_rows_with_profile,
)

__all__ = [
    "DocumentProfile",
    "EconomicSource",
    "ProfileMatch",
    "TableKind",
    "classify_table_kind_via_profile",
    "get_profile",
    "list_profiles",
    "match_profile",
    "parse_rows_with_profile",
]
