"""Scoring determinístico de perfis contra matrizes extraídas do PDF."""

from __future__ import annotations

import re
from typing import Any

from app.domain.profiles.base import DocumentProfile, ProfileMatch

_ITEM_XYZ = re.compile(r"^\d+\.\d+\.\d+")
_ITEM_HIER = re.compile(r"^\d+(?:\.\d+)+$")


def _preview_blob(rows: list[list[Any]], table_name: str = "", limit: int = 10) -> str:
    parts = [table_name.lower()]
    for row in rows[:limit]:
        parts.append(" ".join(str(c).lower() for c in row if str(c).strip()))
    return " ".join(parts)


def _header_blob(rows: list[list[Any]], limit: int = 5) -> str:
    parts: list[str] = []
    for row in rows[:limit]:
        text = " ".join(str(c).lower() for c in row if str(c).strip())
        if any(
            tok in text
            for tok in (
                "item",
                "código",
                "codigo",
                "descri",
                "quant",
                "valor",
                "total",
                "bdi",
                "custo",
                "faixa",
                "incid",
                "acumul",
            )
        ):
            parts.append(text)
    if not parts and rows:
        parts.append(" ".join(str(c).lower() for c in rows[0] if str(c).strip()))
    return " ".join(parts)


def _count_xyz(rows: list[list[Any]], limit: int = 25) -> int:
    n = 0
    for row in rows[:limit]:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if cells and _ITEM_XYZ.match(cells[0]):
            n += 1
    return n


def _count_peso_pct(rows: list[list[Any]], limit: int = 25) -> int:
    n = 0
    for row in rows[:limit]:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if any("%" in c and len(c) < 16 for c in cells[-3:]):
            n += 1
    return n


def _count_composicao_first_col(rows: list[list[Any]], limit: int = 20) -> int:
    n = 0
    for row in rows[:limit]:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if not cells:
            continue
        first = cells[0].lower()
        if first.startswith("composi") or first == "insumo":
            n += 1
    return n


def score_profile_against_rows(
    profile: DocumentProfile,
    rows: list[list[Any]],
    *,
    table_name: str = "",
) -> ProfileMatch:
    if not rows:
        return ProfileMatch(profile.id, 0.0, profile.table_kind, ("empty",))

    blob = _preview_blob(rows, table_name)
    header = _header_blob(rows)
    reasons: list[str] = []
    score = 0.0

    hint_hits = sum(1 for h in profile.detect_hints if h in blob)
    if hint_hits:
        score += min(0.45, 0.15 * hint_hits)
        reasons.append(f"hints:{hint_hits}")

    if profile.header_tokens:
        token_hits = sum(1 for t in profile.header_tokens if t in header)
        ratio = token_hits / max(1, len(profile.header_tokens))
        score += 0.35 * ratio
        if token_hits:
            reasons.append(f"header:{token_hits}/{len(profile.header_tokens)}")

    xyz = _count_xyz(rows)
    peso = _count_peso_pct(rows)
    comp = _count_composicao_first_col(rows)

    if profile.id == "novacap_sintetico":
        if "sintético" in blob or "sintetico" in blob:
            score += 0.35
            reasons.append("title:sintetico")
        if xyz >= 3 and peso >= 3:
            score += 0.4
            reasons.append(f"continuation:xyz={xyz},peso={peso}")
        if "peso" in header and ("valor unit" in header or "total" in header):
            score += 0.15
            reasons.append("cols:peso+total")
        if comp >= 3:
            score -= 0.5
            reasons.append("penalty:composicao")

    elif profile.id == "novacap_planilha":
        if all(t in header for t in ("item", "código" if "código" in header else "codigo", "bdi")) or (
            "item" in header and ("codigo" in header or "código" in header) and "bdi" in header
        ):
            score += 0.3
            reasons.append("cols:item+codigo+bdi")
        if "total c/" in header or "c/ bdi" in header or "com bdi" in header:
            score += 0.25
            reasons.append("cols:total_com_bdi")
        if "fonte" in header or "banco" in header:
            score += 0.1
        if comp >= 3:
            score -= 0.4

    elif profile.id == "bdi_coluna":
        if "bdi" in header and ("quant" in header or "qtd" in header) and (
            "valor" in header or "preço" in header or "preco" in header
        ):
            score += 0.35
            reasons.append("cols:bdi+qtd+valor")
        if re.search(r"\bbdi\s*%|\b%\s*bdi\b|bdi\s*\(%\)", header):
            score += 0.2
            reasons.append("cols:bdi_percent")
        if "peso" in header and "sintético" in blob:
            score -= 0.2

    elif profile.id == "generico_keywords":
        tokens = ("descri", "quant", "valor", "total", "código", "codigo", "item")
        hits = sum(1 for t in tokens if t in header or t in blob)
        score += min(0.4, 0.08 * hits)
        reasons.append(f"generic:{hits}")
        if comp >= 3:
            score -= 0.3

    elif profile.id == "composicao_unitaria":
        if comp >= 3:
            score += 0.45
            reasons.append(f"composicao_rows:{comp}")
        if any(h in blob for h in ("composição", "composicao", "insumo", "coeficiente")):
            score += 0.25
            reasons.append("title:composicao")

    elif profile.id == "orcamento_analitico":
        if any(
            h in blob
            for h in (
                "analítica",
                "analitica",
                "analítico",
                "analitico",
                "orçamento analítico",
                "orcamento analitico",
            )
        ):
            score += 0.4
            reasons.append("title:analitico")
        if "sintético" in blob or "sintetico" in blob:
            score -= 0.35

    elif profile.id == "curva_abc":
        abc_title = "curva abc" in blob
        if abc_title:
            score += 0.45
            reasons.append("title:curva_abc")
        abc_cols = sum(
            1
            for t in ("custo parcial", "custo unit", "faixa", "incid", "acumul")
            if t in header
        )
        if abc_cols >= 2:
            score += 0.35
            reasons.append(f"cols:abc={abc_cols}")
        if "código" in header or "codigo" in header:
            score += 0.1
        # Penaliza layout sintético hierárquico clássico
        if xyz >= 5 and "peso" in header:
            score -= 0.4
            reasons.append("penalty:sintetico_layout")
        if "bdi" in header and "custo parcial" not in header:
            score -= 0.15

    confidence = max(0.0, min(1.0, score))
    return ProfileMatch(
        profile_id=profile.id,
        confidence=confidence,
        table_kind=profile.table_kind,
        reasons=tuple(reasons),
    )
