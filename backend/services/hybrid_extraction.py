"""
Extração híbrida: parser local estrutura colunas/números + IA interpreta semântica.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from budget_parser import BudgetParser

try:
    from app.domain.money import parse_brl
except ImportError:  # pragma: no cover
    parse_brl = None  # type: ignore

_SERVICE_CODE_PATTERN = re.compile(
    r"\b(CPU\d+|[A-Z]{2,}\d{3,}|\d{5,}[A-Z]?)\b",
    re.IGNORECASE,
)

_NUMERIC_FIELDS = ("quantidade", "valor_unitario", "valor_total", "bdi")


def _coerce_number(value: Any) -> float:
    if parse_brl is not None:
        return parse_brl(value)
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R$", "").replace("$", "").replace(" ", "")
    if "." in text and "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def detect_table_structure(rows: List[List[Any]]) -> Dict[str, Any]:
    """Detecta cabeçalho, índices de colunas e itens via BudgetParser."""
    parser = BudgetParser()
    if not rows or len(rows) < 2:
        return {
            "column_indices": {},
            "parser_items": [],
            "header_row_index": -1,
        }

    header_idx = -1
    structure: Dict[str, int] = {}
    for idx, row in enumerate(rows[:25]):
        if parser.is_header_row(row):
            structure = parser.identify_columns(row)
            if structure.get("descricao", -1) != -1 or structure.get("codigo", -1) != -1:
                header_idx = idx
                break

    if header_idx == -1:
        structure = parser.guess_columns_from_data(rows)
        header_idx = 0

    parser_items, _ = parser.parse_table(rows, page=0)
    column_indices = {key: value for key, value in structure.items() if value >= 0}

    return {
        "column_indices": column_indices,
        "parser_items": parser_items,
        "header_row_index": header_idx,
    }


def _normalize_codigo(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().upper())


def _find_parser_match(
    ai_item: Dict[str, Any],
    parser_items: List[Dict[str, Any]],
    index: int,
) -> Dict[str, Any] | None:
    codigo = _normalize_codigo(ai_item.get("codigo"))
    if codigo:
        for parser_item in parser_items:
            if _normalize_codigo(parser_item.get("codigo")) == codigo:
                return parser_item

    descricao = str(ai_item.get("descricao") or "").strip().lower()[:60]
    if descricao:
        for parser_item in parser_items:
            p_desc = str(parser_item.get("descricao") or "").strip().lower()
            if descricao in p_desc or p_desc[:60] in descricao:
                return parser_item

    ai_q = round(_coerce_number(ai_item.get("quantidade")), 4)
    ai_vt = round(_coerce_number(ai_item.get("valor_total")), 2)
    if ai_vt > 0:
        for parser_item in parser_items:
            p_q = round(_coerce_number(parser_item.get("quantidade")), 4)
            p_vt = round(_coerce_number(parser_item.get("valor_total")), 2)
            if abs(p_q - ai_q) < 0.02 and abs(p_vt - ai_vt) < 1.0:
                return parser_item

    if 0 <= index < len(parser_items):
        return parser_items[index]
    return None


def _find_ai_match_for_parser(
    parser_item: Dict[str, Any],
    ai_items: List[Dict[str, Any]],
    index: int,
) -> Dict[str, Any] | None:
    codigo = _normalize_codigo(parser_item.get("codigo"))
    if codigo:
        for ai_item in ai_items:
            if _normalize_codigo(ai_item.get("codigo")) == codigo:
                return ai_item

    p_desc = str(parser_item.get("descricao") or "").strip().lower()[:60]
    if p_desc:
        for ai_item in ai_items:
            ai_desc = str(ai_item.get("descricao") or "").strip().lower()
            if p_desc in ai_desc or ai_desc[:60] in p_desc:
                return ai_item

    p_q = round(_coerce_number(parser_item.get("quantidade")), 4)
    p_vt = round(_coerce_number(parser_item.get("valor_total")), 2)
    if p_vt > 0:
        for ai_item in ai_items:
            ai_q = round(_coerce_number(ai_item.get("quantidade")), 4)
            ai_vt = round(_coerce_number(ai_item.get("valor_total")), 2)
            if abs(p_q - ai_q) < 0.02 and abs(p_vt - ai_vt) < 1.0:
                return ai_item

    if 0 <= index < len(ai_items) and isinstance(ai_items[index], dict):
        return ai_items[index]
    return None


def _numeric_divergence(ai_val: float, parser_val: float, tolerance: float = 0.05) -> bool:
    if parser_val <= 0:
        return False
    if ai_val <= 0:
        return False
    return abs(ai_val - parser_val) / max(abs(parser_val), 1.0) > tolerance


def score_item_confidence(item: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Calcula confiança 0–1 e alertas de validação por linha."""
    alerts: List[str] = []
    score = 1.0

    quantidade = _coerce_number(item.get("quantidade"))
    valor_unitario = _coerce_number(item.get("valor_unitario"))
    valor_total = _coerce_number(item.get("valor_total"))
    codigo = str(item.get("codigo") or "").strip()
    descricao = str(item.get("descricao") or "").strip()

    if not descricao:
        score -= 0.35
        alerts.append("Descrição ausente")
    elif len(descricao) < 4:
        score -= 0.15
        alerts.append("Descrição muito curta")

    if not codigo:
        score -= 0.08
    elif not _SERVICE_CODE_PATTERN.search(codigo) and not re.match(r"^\d+(\.\d+)*$", codigo):
        score -= 0.05

    if quantidade <= 0 and valor_unitario <= 0 and valor_total <= 0:
        score -= 0.4
        alerts.append("Sem quantidade nem preços")
    elif valor_unitario <= 0 and valor_total <= 0:
        score -= 0.25
        alerts.append("Sem preços")

    if quantidade > 0 and valor_unitario > 0 and valor_total > 0:
        esperado = quantidade * valor_unitario
        erro = abs(valor_total - esperado) / max(abs(valor_total), abs(esperado), 1.0)
        if erro > 0.02:
            score -= min(0.35, erro)
            alerts.append(f"Qtd×VU≠Total (diferença {erro:.0%})")

    return max(0.0, min(1.0, round(score, 3))), alerts


def merge_parser_as_primary(
    parser_items: List[Dict[str, Any]],
    ai_items: List[Dict[str, Any]],
    *,
    prefer_parser_numerics: bool = False,
) -> List[Dict[str, Any]]:
    """
    Usa o parser local como base (lista completa) e enriquece com IA (descrição/preços).
    Ideal para editais com colunas de preço em branco.

    prefer_parser_numerics: sintético/edital com VU já c/BDI — não deixa a IA
    injetar BDI% do documento nem alterar totais (causa grupo+filho e reprovações).
    """
    if not parser_items:
        return ai_items

    from app.domain.abc_curve import infer_tipo_linha, normalize_item_numero

    merged: List[Dict[str, Any]] = []
    for index, parser_item in enumerate(parser_items):
        if not isinstance(parser_item, dict):
            continue

        row = dict(parser_item)
        # Sempre normaliza numeração (evita "1.2 DESCRIÇÃO" da IA)
        num = normalize_item_numero(row.get("item_numero") or row.get("item") or "")
        if num:
            row["item_numero"] = num
            row["item"] = num

        ai_match = _find_ai_match_for_parser(parser_item, ai_items, index)

        if ai_match:
            ai_desc = str(ai_match.get("descricao") or "").strip()
            parser_desc = str(row.get("descricao") or "").strip()
            if len(ai_desc) > len(parser_desc):
                row["descricao"] = ai_desc

            if not prefer_parser_numerics:
                qty = _coerce_number(row.get("quantidade"))
                vu = _coerce_number(row.get("valor_unitario"))
                vt = _coerce_number(row.get("valor_total"))
                prices_already_consistent = (
                    qty > 0 and vu > 0 and vt > 0 and abs(qty * vu - vt) / max(vt, 1.0) <= 0.02
                )

                for field in _NUMERIC_FIELDS:
                    ai_val = _coerce_number(ai_match.get(field))
                    parser_val = _coerce_number(row.get(field))
                    if ai_val <= 0:
                        continue
                    # Não injetar BDI do documento quando o total já bate com VU×Qtd
                    if field == "bdi" and prices_already_consistent and parser_val <= 0:
                        continue
                    if parser_val <= 0 or _numeric_divergence(ai_val, parser_val):
                        row[field] = ai_val

            ai_unidade = str(ai_match.get("unidade") or "").strip()
            if ai_unidade and str(row.get("unidade") or "").strip() in ("", "un"):
                row["unidade"] = ai_unidade

        # Tipagem determinística (não forçar "item" em grupos)
        tipo = infer_tipo_linha(
            descricao=str(row.get("descricao") or ""),
            quantidade=_coerce_number(row.get("quantidade")),
            valor_unitario=_coerce_number(row.get("valor_unitario")),
            valor_total=_coerce_number(row.get("valor_total")),
            codigo=str(row.get("codigo") or ""),
            item_numero=str(row.get("item_numero") or row.get("item") or ""),
            tipo_hint=str(row.get("tipo_linha") or row.get("tipo") or ""),
        )
        row["tipo_linha"] = tipo
        row["tipo"] = tipo

        confianca, validation_alerts = score_item_confidence(row)
        row["confianca"] = confianca
        row["alertas"] = validation_alerts
        row["origem_extracao"] = "parser_local_enriquecido_ia" if ai_match else "parser_local"
        merged.append(row)

    return merged


def _looks_hierarchical_item_numero(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return bool(re.match(r"^\d+(?:\.\d+)+$", text))


def merge_ai_with_parser(
    ai_items: List[Dict[str, Any]],
    structure: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Mescla campos numéricos do parser com interpretação semântica da IA."""
    parser_items: List[Dict[str, Any]] = structure.get("parser_items") or []
    if not ai_items:
        return []

    merged: List[Dict[str, Any]] = []
    for index, ai_item in enumerate(ai_items):
        if not isinstance(ai_item, dict):
            continue

        row = dict(ai_item)
        merge_alerts: List[str] = []
        parser_match = _find_parser_match(row, parser_items, index)

        if parser_match:
            parser_desc = str(parser_match.get("descricao") or "").strip()
            ai_desc = str(row.get("descricao") or "").strip()
            if len(parser_desc) > len(ai_desc):
                row["descricao"] = parser_desc

            for field in _NUMERIC_FIELDS:
                ai_val = _coerce_number(row.get(field))
                parser_val = _coerce_number(parser_match.get(field))
                if parser_val <= 0:
                    continue
                if ai_val <= 0:
                    row[field] = parser_val
                    merge_alerts.append(f"{field} preenchido via parser local")
                elif _numeric_divergence(ai_val, parser_val):
                    row[field] = parser_val
                    merge_alerts.append(f"{field}: IA e parser divergiram — usado parser")

            parser_unidade = str(parser_match.get("unidade") or "").strip()
            if parser_unidade and str(row.get("unidade") or "").strip() in ("", "un"):
                row["unidade"] = parser_unidade

            if not str(row.get("codigo") or "").strip() and parser_match.get("codigo"):
                row["codigo"] = parser_match.get("codigo")

            parser_item_numero = str(
                parser_match.get("item_numero") or parser_match.get("item") or ""
            ).strip()
            ai_item_numero = str(row.get("item_numero") or row.get("item") or "").strip()
            if parser_item_numero and (
                not ai_item_numero
                or (
                    _looks_hierarchical_item_numero(parser_item_numero)
                    and not _looks_hierarchical_item_numero(ai_item_numero)
                )
            ):
                row["item_numero"] = parser_item_numero
                row["item"] = parser_item_numero

            parser_banco = str(parser_match.get("banco") or "").strip()
            if parser_banco:
                row["banco"] = parser_banco

        confianca, validation_alerts = score_item_confidence(row)
        all_alerts = merge_alerts + validation_alerts
        row["confianca"] = confianca
        row["alertas"] = all_alerts
        row["origem_extracao"] = "hibrido_parser_ia" if parser_match else "ia"
        merged.append(row)

    return merged


def enrich_hierarchical_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Adiciona confiança/alertas em linhas hierárquicas (grupo/composição)."""
    enriched: List[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        confianca, alerts = score_item_confidence(row)
        row.setdefault("confianca", confianca)
        row.setdefault("alertas", alerts)
        row.setdefault("origem_extracao", row.get("origem_extracao") or "ia")
        enriched.append(row)
    return enriched
