"""
Engine 4 — Normalizer.

Produz JSON canônico independente da origem (SINAPI, SICRO, ORSE, NOVACAP…).
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.abc_curve import enrich_item_pricing_and_type, normalize_item_numero
from app.domain.budget_pipeline.models import HierarchyNode

logger = logging.getLogger(__name__)


def _nivel(item_numero: str) -> int:
    num = normalize_item_numero(item_numero)
    if not num:
        return 0
    return num.count(".") + 1


def build_hierarchy(items: list[dict[str, Any]]) -> list[HierarchyNode]:
    """Monta árvore a partir de numeração 1 / 1.1 / 1.1.1."""
    nodes: dict[str, HierarchyNode] = {}
    order: list[str] = []

    for raw in items:
        num = normalize_item_numero(raw.get("item_numero") or raw.get("item") or "")
        if not num:
            continue
        tipo = str(raw.get("tipo_linha") or raw.get("tipo") or "item").lower()
        node = HierarchyNode(
            item_numero=num,
            codigo=str(raw.get("codigo") or ""),
            descricao=str(raw.get("descricao") or ""),
            unidade=str(raw.get("unidade") or "un"),
            quantidade=float(raw.get("quantidade") or 0),
            valor_unitario=float(
                raw.get("valor_unitario_com_bdi")
                or raw.get("valor_unitario")
                or 0
            ),
            valor_total=float(
                raw.get("valor_total_com_bdi") or raw.get("valor_total") or 0
            ),
            pagina=int(raw.get("pagina") or raw.get("_source_page") or 0),
            nivel=_nivel(num),
            kind="group" if tipo == "grupo" else "item",
        )
        nodes[num] = node
        order.append(num)

    roots: list[HierarchyNode] = []
    attached: set[str] = set()
    for num in order:
        node = nodes[num]
        parent_num = ".".join(num.split(".")[:-1]) if "." in num else ""
        if parent_num and parent_num in nodes:
            nodes[parent_num].filhos.append(node)
            attached.add(num)
        else:
            if num not in attached:
                roots.append(node)

    return roots


def normalize_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """
    Enriquece preços/tipagem e produz:
      - lista plana canônica
      - hierarquia (dict)
    """
    enriched: list[dict[str, Any]] = []
    for raw in items:
        item = enrich_item_pricing_and_type(dict(raw))
        num = normalize_item_numero(item.get("item_numero") or "")
        item["item_numero"] = num
        item["item"] = num
        item["nivel"] = _nivel(num)
        # Schema canônico adicional (camelCase espelhado)
        item["valorUnitario"] = item.get("valor_unitario_com_bdi") or item.get("valor_unitario")
        item["valorTotal"] = item.get("valor_total_com_bdi") or item.get("valor_total")
        item["pagina"] = item.get("pagina") or item.get("_source_page") or 0
        enriched.append(item)

    hierarchy = [n.to_dict() for n in build_hierarchy(enriched)]
    meta = {
        "engine": "normalizer",
        "items": len(enriched),
        "hierarchy_roots": len(hierarchy),
    }
    logger.info("[engine4] items=%s roots=%s", len(enriched), len(hierarchy))
    return enriched, hierarchy, meta


def incomplete_to_dict(rows: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "item_numero": getattr(row, "item_numero", ""),
                "codigo": getattr(row, "codigo", ""),
                "descricao": getattr(row, "descricao", ""),
                "pagina": getattr(row, "page", 0),
                "motivo": getattr(row, "incomplete_reason", "incompleto"),
                "cells": getattr(row, "cells", []),
            }
        )
    return out
