"""Adapters de linha: encapsulam BudgetParser por perfil."""

from __future__ import annotations

from typing import Any

from budget_parser import BudgetParser


def _to_items(
    parsed: list[dict[str, Any]],
    *,
    page: int,
    profile_id: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for idx, raw in enumerate(parsed, start=1):
        if not isinstance(raw, dict):
            continue
        descricao = str(raw.get("descricao") or "").strip()
        if len(descricao) < 3:
            continue
        item = dict(raw)
        item.setdefault("id", f"{profile_id}_{page}_{idx}")
        item.setdefault("item", item.get("item_numero") or item.get("item") or "")
        item.setdefault("item_numero", item.get("item") or "")
        item["_profile_id"] = profile_id
        item["_source_page"] = page
        out.append(item)
    return out


def parse_with_budget_parser(
    rows: list[list[Any]],
    page: int = 0,
    *,
    profile_id: str = "generico_keywords",
    prefer_row_scan: bool = False,
) -> list[dict[str, Any]]:
    """Parse genérico: cabeçalho + fallback NOVACAP/sintético do BudgetParser."""
    parser = BudgetParser()
    parsed, _ = parser.parse_table(rows, page=page)
    if prefer_row_scan or len(parsed) < 3:
        scanned = parser.parse_table_row_scan(rows, page=page)
        if len(scanned) > len(parsed):
            parsed = scanned
    return _to_items(parsed, page=page, profile_id=profile_id)


def parse_novacap_sintetico(rows: list[list[Any]], page: int = 0) -> list[dict[str, Any]]:
    """Orçamento Sintético / continuação com Peso %."""
    parser = BudgetParser()
    # Garante injeção de cabeçalho em continuações
    parsed, structure = parser.parse_table(rows, page=page)
    # Reforço: varredura linha a linha sintético
    scanned: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not row or parser.is_header_row(row) or parser.should_ignore_row(row):
            continue
        hit = parser.try_parse_sintetico_row(row)
        if hit:
            hit["id"] = f"sint_{page}_{idx}"
            hit["origem"] = f"página {page}, linha {idx}"
            scanned.append(hit)
    # Prefere o conjunto com mais itens com valor_total > 0
    def _value_count(items: list[dict[str, Any]]) -> int:
        return sum(1 for i in items if float(i.get("valor_total") or 0) > 0)

    if _value_count(scanned) > _value_count(parsed):
        parsed = scanned
    elif not parsed and scanned:
        parsed = scanned

    # Se parse_table achou estrutura boa, mescla por item_numero preferindo total maior
    if parsed and scanned and structure:
        by_num: dict[str, dict[str, Any]] = {}
        for item in parsed + scanned:
            key = str(item.get("item_numero") or item.get("item") or "").strip()
            if not key:
                continue
            prev = by_num.get(key)
            if not prev or float(item.get("valor_total") or 0) >= float(
                prev.get("valor_total") or 0
            ):
                by_num[key] = item
        parsed = list(by_num.values())

    return _to_items(parsed, page=page, profile_id="novacap_sintetico")


def parse_novacap_planilha(rows: list[list[Any]], page: int = 0) -> list[dict[str, Any]]:
    """Planilha Item|Fonte|Código|…|BDI|Total c/BDI."""
    parser = BudgetParser()
    parsed, _ = parser.parse_table(rows, page=page)
    scanned = parser.parse_table_row_scan(rows, page=page)
    if len(scanned) > len(parsed):
        parsed = scanned
    return _to_items(parsed, page=page, profile_id="novacap_planilha")


def parse_bdi_coluna(rows: list[list[Any]], page: int = 0) -> list[dict[str, Any]]:
    """Layout com coluna BDI % explícita + total."""
    return parse_with_budget_parser(
        rows, page, profile_id="bdi_coluna", prefer_row_scan=False
    )


def parse_generico(rows: list[list[Any]], page: int = 0) -> list[dict[str, Any]]:
    return parse_with_budget_parser(
        rows, page, profile_id="generico_keywords", prefer_row_scan=True
    )
