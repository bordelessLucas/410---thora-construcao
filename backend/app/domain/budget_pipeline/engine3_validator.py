"""
Engine 3 — Financial Validator.

Confere subtotais, Total Geral, duplicidades. Tol. R$ 0,01.
Divergência vs Total Geral explícito → rejeita (ok=False).
Se Total Geral ausente: infere da soma das folhas (com warning) quando
possível — permite PDFs sem capa/resumo.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.abc_curve import normalize_item_numero
from app.domain.budget_pipeline.models import RawBudgetRow, ValidationReport
from app.domain.financial_validation import (
    reconcile_leaves_to_document_total,
    select_executive_leaves,
    validate_group_subtotals,
    validate_total_geral,
)

logger = logging.getLogger(__name__)

ABS_TOLERANCE = 0.01


def _row_to_dict(row: RawBudgetRow) -> dict[str, Any]:
    return {
        "item_numero": normalize_item_numero(row.item_numero),
        "item": normalize_item_numero(row.item_numero),
        "codigo": row.codigo,
        "banco": row.banco,
        "descricao": row.descricao,
        "unidade": row.unidade or "un",
        "quantidade": row.quantidade,
        "valor_unitario": row.valor_unitario,
        "valor_total": row.valor_total,
        "valor_total_com_bdi": row.valor_total,
        "valor_unitario_sem_bdi": row.valor_unitario,
        "bdi": row.bdi if row.bdi > 0 else 0.0,
        "tipo_linha": "grupo" if row.kind == "group" else "item",
        "tipo": "grupo" if row.kind == "group" else "item",
        "_source_page": row.page,
        "pagina": row.page,
    }


def _infer_document_total(
    items: list[dict[str, Any]], soma_folhas: float
) -> tuple[float | None, str]:
    """Infere Total Geral a partir de grupos raiz ou da própria soma das folhas."""
    roots: list[dict[str, Any]] = []
    for item in items:
        num = normalize_item_numero(item.get("item_numero") or "")
        tipo = str(item.get("tipo_linha") or item.get("tipo") or "").lower()
        if tipo == "grupo" and num and "." not in num:
            roots.append(item)
    if roots:
        soma_grupos = sum(
            float(r.get("valor_total_com_bdi") or r.get("valor_total") or 0) for r in roots
        )
        if soma_grupos > 0 and abs(soma_grupos - soma_folhas) <= ABS_TOLERANCE:
            return soma_grupos, "inferido_grupos_raiz"
    if soma_folhas > 0:
        return soma_folhas, "inferido_soma_folhas"
    return None, ""


def validate_financial(
    rows: list[RawBudgetRow],
    *,
    document_total: float | None,
) -> tuple[list[dict[str, Any]], ValidationReport, dict[str, Any]]:
    """
    Valida soma das folhas vs Total Geral e subtotais de grupo.

    Retorna (items_dicts reconciliados, report, meta).
    Se report.ok is False, o runner deve rejeitar a extração.
    """
    items = [_row_to_dict(r) for r in rows]

    # Remove duplicatas por item_numero+código (mantém maior total).
    # Mesmo nº com códigos/descrições diferentes = serviços distintos (PDF mal numerado).
    by_key: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    for item in items:
        num = normalize_item_numero(item.get("item_numero") or "")
        if not num:
            orphans.append(item)
            continue
        codigo = str(item.get("codigo") or "").strip().upper()
        desc = str(item.get("descricao") or "").strip().lower()[:40]
        key = f"{num}::{codigo}" if codigo else f"{num}::{desc}"
        prev = by_key.get(key)
        if not prev or float(item.get("valor_total") or 0) >= float(prev.get("valor_total") or 0):
            by_key[key] = item
    items = list(by_key.values()) + orphans

    explicit_total = document_total is not None and document_total > 0
    inferred_from = ""

    if explicit_total:
        items = reconcile_leaves_to_document_total(
            items, float(document_total), abs_tolerance=ABS_TOLERANCE
        )

    total_check = validate_total_geral(
        items,
        document_total,
        abs_tolerance=ABS_TOLERANCE,
        relative_tolerance=0.0,
    )

    errors: list[str] = []
    warnings: list[str] = []

    if explicit_total:
        soma = float(total_check.get("soma_folhas") or 0)
        diff = abs(soma - float(document_total))
        total_check["diferenca"] = round(diff, 2)
        # Tol. estrita de centavo — orçamento público não admite erro residual.
        allowed = ABS_TOLERANCE
        total_check["ok"] = diff <= allowed
        if not total_check["ok"]:
            total_check["alerta"] = (
                f"Soma das folhas ({soma:,.2f}) ≠ Total Geral ({document_total:,.2f}) "
                f"— diferença {diff:,.2f}"
            ).replace(",", "X").replace(".", ",").replace("X", ".")
            errors.append(str(total_check["alerta"]))
    else:
        soma = float(total_check.get("soma_folhas") or 0)
        inferred, inferred_from = _infer_document_total(items, soma)
        if inferred and inferred > 0:
            document_total = inferred
            total_check["ok"] = True
            total_check["diferenca"] = 0.0
            total_check["total_geral_documento"] = inferred
            warnings.append(
                f"Total Geral do documento não encontrado — usando {inferred_from} "
                f"({inferred:,.2f})".replace(",", "X").replace(".", ",").replace("X", ".")
            )
        else:
            warnings.append("Total Geral do documento não encontrado")
            errors.append(
                "Não foi possível determinar o Total Geral nem inferir das folhas"
            )

    subtotal_check = validate_group_subtotals(
        items, abs_tolerance=ABS_TOLERANCE, relative_tolerance=0.0
    )
    hard_mismatches = [
        m
        for m in (subtotal_check.get("mismatches") or [])
        if float(m.get("diferenca") or 0) > ABS_TOLERANCE
    ]
    if hard_mismatches and explicit_total:
        errors.append(
            f"{len(hard_mismatches)} subtotal(is) inconsistente(s) — extração rejeitada"
        )
    elif hard_mismatches:
        warnings.append(
            f"{len(hard_mismatches)} subtotal(is) inconsistente(s) "
            "(aviso — Total Geral inferido)"
        )

    seen: set[str] = set()
    for item in items:
        key = f"{item.get('codigo')}::{item.get('item_numero')}"
        if key in seen and item.get("codigo"):
            warnings.append(f"Possível duplicata: {key}")
        seen.add(key)

    leaves = select_executive_leaves(items)
    report = ValidationReport(
        ok=len(errors) == 0 and bool(document_total and document_total > 0),
        total_geral_documento=document_total,
        soma_folhas=float(total_check.get("soma_folhas") or 0),
        diferenca_total=total_check.get("diferenca"),
        subtotal_mismatches=hard_mismatches if explicit_total else [],
        errors=errors,
        warnings=warnings,
    )

    meta = {
        "engine": "financial_validator",
        "ok": report.ok,
        "folhas": len(leaves),
        "soma_folhas": report.soma_folhas,
        "document_total": document_total,
        "explicit_total": explicit_total,
        "inferred_from": inferred_from,
        "errors": errors,
        "warnings": warnings,
    }
    logger.info(
        "[engine3] ok=%s folhas=%s soma=%s total_doc=%s explicit=%s errors=%s",
        report.ok,
        len(leaves),
        report.soma_folhas,
        document_total,
        explicit_total,
        errors,
    )
    return items, report, meta
