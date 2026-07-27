"""
Normalização pós-extração do Orçamento Analítico (100% fiel ao PDF, sem mock).

- Remove linhas genéricas conhecidas (alucinações comuns da IA)
- Grupo: sem valor_unitario E sem quantidade (ignora unidade)
- Numeração: prefixo do grupo (ex: 02) + filhos 02.01, 02.02…
- Total = Qtd × VU × (1 + BDI/100); rollup nos grupos
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from app.domain.money import parse_brl

_GROUP_PREFIX_RE = re.compile(r"^\s*(\d+)")
_GROUP_PREFIX_DESC_RE = re.compile(r"^\s*(\d+)\s*[-–—\.\)]")

_HALLUCINATION_DESC_PATTERNS = (
    re.compile(r"servi[cç]o de instala[cç][aã]o de cabos el[eé]tricos", re.IGNORECASE),
    re.compile(r"instala[cç][aã]o de cabos el[eé]tricos", re.IGNORECASE),
    re.compile(r"fornecimento de lumin[aá]rias?\s*led", re.IGNORECASE),
    re.compile(r"fornecimento de concreto usinado", re.IGNORECASE),
    re.compile(r"fornecimento de materiais de constru[cç][aã]o", re.IGNORECASE),
    re.compile(r"m[aã]o de obra para pintura", re.IGNORECASE),
    re.compile(r"pintura de paredes internas", re.IGNORECASE),
)

_GENERIC_UNCODED_PHANTOM_PREFIXES = (
    re.compile(r"^fornecimento de ", re.IGNORECASE),
    re.compile(r"^m[aã]o de obra para ", re.IGNORECASE),
    re.compile(r"^servi[cç]o de instala[cç][aã]o de ", re.IGNORECASE),
)


def parse_numeric(value: Any) -> float:
    """Alias canônico — sempre usa parse_brl (nunca remove todos os pontos)."""
    return parse_brl(value)


def _coerce_number(value: Any) -> float:
    return parse_brl(value)


def _coerce_bdi(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        raw = float(value)
    else:
        text = str(value).strip().replace("%", "").replace(" ", "")
        raw = parse_brl(text)
    if 0 < raw <= 100:
        return raw
    return 0.0


def _row_descricao(row: Dict[str, Any]) -> str:
    return str(row.get("descricao") or row.get("description") or "").strip()


def _row_codigo(row: Dict[str, Any]) -> str:
    return str(row.get("codigo") or row.get("code") or "").strip()


def is_hallucinated_test_row(row: Dict[str, Any]) -> bool:
    """Remove itens genéricos típicos de alucinação da IA (não vêm do edital real)."""
    if _row_codigo(row):
        return False
    desc = _row_descricao(row)
    if not desc:
        return False
    if any(pattern.search(desc) for pattern in _HALLUCINATION_DESC_PATTERNS):
        return True
    desc_lower = desc.lower()
    if any(pattern.match(desc_lower) for pattern in _GENERIC_UNCODED_PHANTOM_PREFIXES):
        return True
    qty = _coerce_number(row.get("quantidade") or row.get("qty"))
    vu = _coerce_number(
        row.get("valor_unitario")
        or row.get("valor_unitário")
        or row.get("unitPrice")
    )
    if qty == 50.0 and vu == 200.0 and "concreto" in desc_lower and "fornecimento" in desc_lower:
        return True
    return False


def filter_faithful_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Mantém apenas dicts válidos; exclui linhas de teste/mock conhecidas."""
    faithful: List[Dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        if is_hallucinated_test_row(raw):
            continue
        faithful.append(raw)
    return faithful


def classify_tipo_linha(row: Dict[str, Any]) -> str:
    """
    Sem valor_unitario (vazio/null/zero) E sem quantidade → grupo.
    Unidade é ignorada na classificação. Nunca retorna vazio/None.
    """
    quantidade = parse_numeric(row.get("quantidade") or row.get("qty"))
    valor_unitario = parse_numeric(
        row.get("valor_unitario")
        or row.get("valor_unitário")
        or row.get("unitPrice")
        or row.get("unit_com_bdi")
    )

    if quantidade <= 0 and valor_unitario <= 0:
        return "grupo"

    tipo_atual = str(row.get("tipo_linha") or row.get("tipo") or "item").strip().lower()
    if not tipo_atual or tipo_atual in ("none", "null"):
        tipo_atual = "item"
    if tipo_atual in ("composicao", "composição", "insumo", "subitem"):
        return "composicao"
    return "item"


def force_grupo_typing(row: Dict[str, Any]) -> str:
    """Garante tipo_linha/tipo como string explícita ('grupo', 'item' ou 'composicao')."""
    tipo = classify_tipo_linha(row)
    row["tipo_linha"] = tipo
    row["tipo"] = tipo

    if tipo == "grupo":
        row["quantidade"] = 0.0
        row["qty"] = 0.0
        row["valor_unitario"] = 0.0
        row["unitPrice"] = 0.0
        row["unit_com_bdi"] = 0.0
    return tipo


def _apply_grupo_total(rows: List[Dict[str, Any]], index: int, total: float) -> None:
    if index < 0 or index >= len(rows):
        return
    row = rows[index]
    rounded = round(float(total), 2)
    row["valor_total"] = rounded
    row["total_com_bdi"] = rounded
    row["totalValue"] = rounded
    row["tipo_linha"] = "grupo"
    row["tipo"] = "grupo"
    row["quantidade"] = 0.0
    row["qty"] = 0.0
    row["valor_unitario"] = 0.0
    row["unitPrice"] = 0.0
    row["unit_com_bdi"] = 0.0
    row["unidade"] = ""
    row["unit"] = ""


def accumulate_grupo_totals_bottom_up(rows: List[Dict[str, Any]]) -> None:
    """
    Soma bottom-up: ao encontrar grupo, fecha o anterior; itens alimentam soma_grupo.
    Usa parse_numeric para evitar concatenação de strings formatadas (pt-BR).
    """
    grupo_atual_index = -1
    soma_grupo = 0.0

    for i, row in enumerate(rows):
        tipo = force_grupo_typing(row)

        if tipo == "grupo":
            if grupo_atual_index >= 0:
                _apply_grupo_total(rows, grupo_atual_index, soma_grupo)
            grupo_atual_index = i
            soma_grupo = 0.0
            continue

        apply_item_totals(row)
        soma_grupo += parse_numeric(row.get("valor_total") or 0)

    if grupo_atual_index >= 0:
        _apply_grupo_total(rows, grupo_atual_index, soma_grupo)


def extract_group_prefix(row: Dict[str, Any]) -> Optional[str]:
    """Extrai prefixo numérico (ex: '02' de '02 - CANTEIRO' ou item_numero '02')."""
    for field in (
        str(row.get("item_numero") or row.get("item") or "").strip(),
        _row_descricao(row),
    ):
        if not field:
            continue
        match = _GROUP_PREFIX_RE.match(field)
        if match:
            return match.group(1)
        match_desc = _GROUP_PREFIX_DESC_RE.match(field)
        if match_desc:
            return match_desc.group(1)
    return None


def compute_line_total(row: Dict[str, Any], *, tipo: str) -> float:
    if tipo == "grupo":
        return 0.0

    qty = _coerce_number(row.get("quantidade") or row.get("qty"))
    vu = _coerce_number(
        row.get("valor_unitario")
        or row.get("valor_unitário")
        or row.get("unitPrice")
        or row.get("unit_com_bdi")
    )
    if qty > 0 and vu > 0:
        bdi = _coerce_bdi(row.get("bdi") or row.get("BDI"))
        factor = 1.0 + (bdi / 100.0) if bdi > 0 else 1.0
        return round(qty * vu * factor, 2)

    explicit = _coerce_number(
        row.get("valor_total") or row.get("total_com_bdi") or row.get("totalValue")
    )
    return explicit if explicit > 0 else 0.0


def apply_item_totals(row: Dict[str, Any]) -> None:
    """Garante valor_total = Qtd × VU (× BDI se houver) em itens/composições."""
    tipo = str(row.get("tipo_linha") or "item")
    if tipo == "grupo":
        return
    total = compute_line_total(row, tipo=tipo)
    row["valor_total"] = total
    row["total_com_bdi"] = total
    row["totalValue"] = total


def assign_hierarchical_numbers(rows: List[Dict[str, Any]]) -> None:
    """
    Numeração inteligente: grupo define prefixo; itens/composições recebem PP.NN (02.01…).
    Não preserva numeração contínua errada da IA (1.1 … 1.146).
    """
    current_group_prefix = ""
    child_counter = 1
    orphan_group_seq = 0

    for row in rows:
        tipo = str(row.get("tipo_linha") or "item")

        if tipo == "grupo":
            prefix = extract_group_prefix(row)
            if prefix:
                current_group_prefix = prefix
            else:
                orphan_group_seq += 1
                current_group_prefix = str(orphan_group_seq)
            child_counter = 1
            row["item_numero"] = current_group_prefix
            row["item"] = current_group_prefix
            continue

        if tipo in ("item", "composicao"):
            if not current_group_prefix:
                orphan_group_seq += 1
                current_group_prefix = str(orphan_group_seq)
                child_counter = 1

            numbered = f"{current_group_prefix}.{child_counter:02d}"
            row["item_numero"] = numbered
            row["item"] = numbered
            child_counter += 1


def rollup_group_totals(rows: List[Dict[str, Any]]) -> None:
    """Alias — delega ao laço bottom-up com parse_numeric."""
    accumulate_grupo_totals_bottom_up(rows)


SINTETICO_MAX_DESCRICAO_GRUPO = 100


def _row_valor_total(row: Dict[str, Any]) -> float:
    return parse_numeric(row.get("valor_total") or row.get("total_com_bdi") or 0)


def is_legitimate_sintetico_grupo(row: Dict[str, Any]) -> bool:
    """Filtra cláusulas longas e grupos sem total consolidado."""
    if str(row.get("tipo_linha") or "").lower() != "grupo":
        return False
    desc = _row_descricao(row)
    if len(desc) > SINTETICO_MAX_DESCRICAO_GRUPO:
        return False
    return _row_valor_total(row) > 0.0


def calcular_totais_sinteticos(rows: List[Dict[str, Any]]) -> None:
    """Reconsolida totais de grupo (bottom-up + parse_numeric)."""
    accumulate_grupo_totals_bottom_up(rows)


def build_sintetico_grupo_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Grupos pai consolidados e sanitizados para exportação sintética."""
    if not items:
        return []

    payload: List[Dict[str, Any]] = []
    for idx, raw in enumerate(items):
        if not isinstance(raw, dict):
            continue
        payload.append({**raw, "_order": idx})

    normalized = normalize_hierarchical_analitico(payload)
    calcular_totais_sinteticos(normalized)

    sintetico: List[Dict[str, Any]] = []
    for row in normalized:
        if not is_legitimate_sintetico_grupo(row):
            continue
        total = _row_valor_total(row)
        sintetico.append(
            {
                "item_numero": str(row.get("item_numero") or row.get("item") or "").strip(),
                "descricao": _row_descricao(row),
                "valor_total": total,
                "total_com_bdi": total,
            }
        )
    return sintetico


def normalize_hierarchical_analitico(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not items:
        return []

    rows: List[Dict[str, Any]] = []
    for raw in filter_faithful_rows(items):
        row = dict(raw)
        force_grupo_typing(row)

        if str(row.get("tipo_linha")) != "grupo":
            unidade = str(row.get("unidade") or row.get("unit") or "").strip()
            row["unidade"] = unidade or "un"
            row["unit"] = row["unidade"]
            apply_item_totals(row)

        rows.append(row)

    assign_hierarchical_numbers(rows)
    for row in rows:
        if str(row.get("tipo_linha")) != "grupo":
            apply_item_totals(row)
    accumulate_grupo_totals_bottom_up(rows)
    return rows
