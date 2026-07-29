"""
Validação financeira pós-extração (Etapas 10–11 do pipeline por coordenadas).

- Soma dos totais das folhas vs Total Geral do documento
- Subtotais de grupo: soma dos filhos ≈ total do pai
- Reconciliação automática de contagem dupla (grupo+filho)
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.money import parse_brl, relative_error

logger = logging.getLogger(__name__)


def _item_numero(item: dict[str, Any]) -> str:
    from app.domain.abc_curve import normalize_item_numero

    return normalize_item_numero(item.get("item_numero") or item.get("item") or "")


def _line_total(item: dict[str, Any]) -> float:
    return parse_brl(
        item.get("valor_total_com_bdi")
        if item.get("valor_total_com_bdi") is not None
        else item.get("valor_total") or item.get("lineTotal")
    )


def _is_leaf(item_numero: str, all_numbers: set[str]) -> bool:
    if not item_numero:
        return True
    prefix = item_numero + "."
    return not any(n.startswith(prefix) for n in all_numbers)


def select_executive_leaves(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Folhas com valor > 0 (base da Curva ABC / validação de total).

    Inclui serviços precificados (tipo=item com qtd e VU) mesmo quando a
    numeração do PDF cria um "filho" fantasma (ex.: 3.1.1 e 3.1.1.1).
    """
    valued = [i for i in items if isinstance(i, dict) and _line_total(i) > 0]
    numbers = {_item_numero(i) for i in valued if _item_numero(i)}
    out: list[dict[str, Any]] = []
    for item in valued:
        num = _item_numero(item)
        tipo = str(item.get("tipo_linha") or item.get("tipo") or "").lower()
        qty = float(item.get("quantidade") or 0)
        vu = float(
            item.get("valor_unitario_com_bdi")
            or item.get("valor_unitario")
            or 0
        )
        codigo = str(item.get("codigo") or "").strip()
        if tipo == "item" and qty > 0 and vu > 0 and (
            codigo or abs(vu - _line_total(item)) > 0.02
        ):
            out.append(item)
            continue
        if _is_leaf(num, numbers):
            out.append(item)
    return out


def validate_total_geral(
    items: list[dict[str, Any]],
    expected_total: float | None,
    *,
    abs_tolerance: float = 0.01,
    relative_tolerance: float = 0.001,
) -> dict[str, Any]:
    """
    Etapa 10: soma dos totais das folhas vs Total Geral do PDF.

    Aceita se |diff| <= abs_tolerance OU erro relativo <= relative_tolerance.
    """
    leaves = select_executive_leaves(items)
    soma = round(sum(_line_total(i) for i in leaves), 2)
    result: dict[str, Any] = {
        "ok": True,
        "soma_folhas": soma,
        "total_geral_documento": expected_total,
        "diferenca": None,
        "folhas": len(leaves),
        "alerta": None,
    }
    if expected_total is None or expected_total <= 0:
        result["ok"] = True
        result["alerta"] = "Total Geral do documento não encontrado — validação parcial"
        return result

    diff = abs(soma - expected_total)
    rel = relative_error(expected_total, soma)
    result["diferenca"] = round(diff, 2)
    result["ok"] = diff <= abs_tolerance or rel <= relative_tolerance
    if not result["ok"]:
        result["alerta"] = (
            f"Soma das folhas ({soma:,.2f}) ≠ Total Geral ({expected_total:,.2f}) "
            f"— diferença {diff:,.2f}"
        ).replace(",", "X").replace(".", ",").replace("X", ".")
    return result


def validate_group_subtotals(
    items: list[dict[str, Any]],
    *,
    abs_tolerance: float = 0.05,
    relative_tolerance: float = 0.002,
) -> dict[str, Any]:
    """
    Etapa 11: para cada pai com total, soma dos filhos diretos deve bater.

    Ex.: 1.4 = 1.4.1 + 1.4.2 + …
    """
    by_num: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        num = _item_numero(item)
        if not num:
            continue
        prev = by_num.get(num)
        if not prev or _line_total(item) >= _line_total(prev):
            by_num[num] = item

    mismatches: list[dict[str, Any]] = []

    for num, parent in by_num.items():
        parent_total = _line_total(parent)
        if parent_total <= 0:
            continue
        prefix = num + "."
        children = []
        for child_num, child in by_num.items():
            if not child_num.startswith(prefix):
                continue
            rest = child_num[len(prefix) :]
            if "." in rest:
                continue
            children.append(child)
        if len(children) < 2:
            continue
        child_sum = round(sum(_line_total(c) for c in children), 2)
        diff = abs(child_sum - parent_total)
        rel = relative_error(parent_total, child_sum)
        if diff > abs_tolerance and rel > relative_tolerance:
            mismatches.append(
                {
                    "item_numero": num,
                    "total_grupo": parent_total,
                    "soma_filhos": child_sum,
                    "diferenca": round(diff, 2),
                    "filhos": [_item_numero(c) for c in children],
                }
            )

    grupos_verificados = 0
    for num, parent in by_num.items():
        if _line_total(parent) <= 0:
            continue
        direct = [
            c
            for c in by_num
            if c.startswith(num + ".") and "." not in c[len(num) + 1 :]
        ]
        if len(direct) >= 2:
            grupos_verificados += 1

    return {
        "ok": len(mismatches) == 0,
        "grupos_verificados": grupos_verificados,
        "mismatches": mismatches,
        "alerta": (
            None
            if not mismatches
            else f"{len(mismatches)} subtotal(is) inconsistente(s) — possível erro de leitura"
        ),
    }


def reconcile_leaves_to_document_total(
    items: list[dict[str, Any]],
    expected_total: float | None,
    *,
    abs_tolerance: float = 1.0,
) -> list[dict[str, Any]]:
    """
    Se a soma das folhas exceder o Total Geral por ~valor de um pai hierárquico,
    remove esse pai (contagem dupla grupo+filho).
    """
    if expected_total is None or expected_total <= 0:
        return items

    leaves = select_executive_leaves(items)
    soma = round(sum(_line_total(i) for i in leaves), 2)
    excess = round(soma - expected_total, 2)
    if excess <= abs_tolerance:
        return items

    all_numbers = {_item_numero(i) for i in items if _item_numero(i)}
    removable: list[dict[str, Any]] = []
    for leaf in leaves:
        num = _item_numero(leaf)
        value = _line_total(leaf)
        if abs(value - excess) > max(abs_tolerance, 0.5):
            continue
        has_children = bool(num) and any(n.startswith(num + ".") for n in all_numbers)
        depth = num.count(".") if num else 99
        if has_children or depth <= 1:
            removable.append(leaf)

    if not removable:
        return items

    removable.sort(key=lambda i: _line_total(i), reverse=True)
    victim = removable[0]
    drop_num = _item_numero(victim)
    logger.warning(
        "Reconciliando Total Geral: removendo item %s (R$ %.2f) — excesso R$ %.2f",
        drop_num,
        _line_total(victim),
        excess,
    )
    return [
        i
        for i in items
        if not (
            _item_numero(i) == drop_num
            and abs(_line_total(i) - _line_total(victim)) < 0.02
        )
    ]


def validate_extraction_finance(
    items: list[dict[str, Any]],
    *,
    expected_total: float | None = None,
    abs_tolerance: float = 0.01,
) -> dict[str, Any]:
    """Combina validação de Total Geral + subtotais de grupo."""
    total_check = validate_total_geral(
        items, expected_total, abs_tolerance=abs_tolerance
    )
    subtotal_check = validate_group_subtotals(items, abs_tolerance=max(abs_tolerance, 0.05))
    alerts = [a for a in (total_check.get("alerta"), subtotal_check.get("alerta")) if a]
    return {
        "ok": bool(total_check["ok"] and subtotal_check["ok"]),
        "total_geral": total_check,
        "subtotais": subtotal_check,
        "alertas": alerts,
    }
