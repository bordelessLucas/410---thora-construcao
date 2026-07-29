"""Registry e matching de perfis de documento."""

from __future__ import annotations

from typing import Any

from app.domain.profiles.base import DocumentProfile, ProfileMatch, TableKind
from app.domain.profiles.definitions import ALL_PROFILES, GENERICO


def list_profiles() -> tuple[DocumentProfile, ...]:
    return ALL_PROFILES


def get_profile(profile_id: str) -> DocumentProfile | None:
    for profile in ALL_PROFILES:
        if profile.id == profile_id:
            return profile
    return None


def match_profile(
    rows: list[list[Any]],
    *,
    table_name: str = "",
    min_confidence: float = 0.25,
) -> ProfileMatch:
    """
    Escolhe o perfil com maior confiança.
    Empate: prefere preferred_for_abc, depois id estável.
    """
    if not rows:
        return ProfileMatch(
            profile_id=GENERICO.id,
            confidence=0.0,
            table_kind=GENERICO.table_kind,
            reasons=("empty",),
        )

    scored: list[tuple[ProfileMatch, DocumentProfile]] = []
    for profile in ALL_PROFILES:
        hit = profile.score_rows(rows, table_name=table_name)
        scored.append((hit, profile))

    scored.sort(
        key=lambda pair: (
            pair[0].confidence,
            1 if pair[1].preferred_for_abc else 0,
            # Desempate: sintético > planilha orçamento > resto
            2 if pair[1].table_kind == "sintetico" else (
                1 if pair[1].table_kind == "orcamento" else 0
            ),
        ),
        reverse=True,
    )

    best_match, best_profile = scored[0]
    if best_match.confidence < min_confidence:
        # Só preserva kind “forte” se houver algum sinal real
        if (
            best_match.confidence > 0
            and best_profile.table_kind in {"composicao", "analitico", "sintetico"}
        ):
            return best_match
        return ProfileMatch(
            profile_id=GENERICO.id,
            confidence=best_match.confidence,
            table_kind=GENERICO.table_kind,
            reasons=best_match.reasons + ("fallback:generico",),
        )
    return best_match


def classify_table_kind_via_profile(
    rows: list[list[Any]],
    *,
    table_name: str = "",
) -> TableKind:
    return match_profile(rows, table_name=table_name).table_kind


def parse_rows_with_profile(
    rows: list[list[Any]],
    *,
    page: int = 0,
    table_name: str = "",
    profile_id: str | None = None,
) -> tuple[list[dict[str, Any]], ProfileMatch]:
    """Parse usando adapter do perfil matched (ou forçado)."""
    if profile_id:
        profile = get_profile(profile_id) or GENERICO
        match = ProfileMatch(
            profile_id=profile.id,
            confidence=1.0,
            table_kind=profile.table_kind,
            reasons=("forced",),
        )
    else:
        match = match_profile(rows, table_name=table_name)
        profile = get_profile(match.profile_id) or GENERICO

    adapter = profile.parse_rows
    if adapter is None:
        from app.domain.profiles.adapters import parse_generico

        items = parse_generico(rows, page)
    else:
        items = adapter(rows, page)

    for item in items:
        item["_profile_id"] = profile.id
        item["_table_kind"] = profile.table_kind
    return items, match
