"""
Curva ABC canônica (Pareto 80/95) + tipagem determinística de linhas.

Fonte de verdade para classificação A/B/C no backend.
"""

from __future__ import annotations

import re
from typing import Any, Iterable

from app.domain.money import parse_brl, resolve_pricing_contract

_SUBTOTAL_KEYWORDS = (
    "total geral",
    "subtotal",
    "total do grupo",
    "total:",
    "suma",
    "grand total",
)

_COMPOSICAO_KEYWORDS = (
    "insumo",
    "composicao",
    "composição",
    "material:",
    "mao de obra",
    "mão de obra",
    "equipamento:",
)

_ITEM_NUM_EXEC = re.compile(r"^\d+\.\d+\.\d+")
_ITEM_NUM_GROUP = re.compile(r"^\d+(?:\.\d+)?$")


def infer_tipo_linha(
    *,
    descricao: str = "",
    quantidade: float = 0.0,
    valor_unitario: float = 0.0,
    valor_total: float = 0.0,
    codigo: str = "",
    item_numero: str = "",
    tipo_hint: str = "",
) -> str:
    """
    Tipagem determinística NOVACAP/edital:
    - X.Y.Z + financeiro → item executivo
    - só X ou X.Y sem financeiro → grupo
    - sinais fortes de insumo → composicao
    """
    desc = (descricao or "").strip()
    desc_norm = desc.lower()
    item_stripped = (item_numero or "").strip()
    codigo = (codigo or "").strip()
    hint = (tipo_hint or "").strip().lower()

    if any(kw in desc_norm for kw in _SUBTOTAL_KEYWORDS):
        return "grupo"

    has_financial = quantidade > 0 or valor_unitario > 0 or valor_total > 0
    is_xyz = bool(item_stripped and _ITEM_NUM_EXEC.match(item_stripped))

    # NOVACAP: serviço executivo padrão
    if is_xyz and (valor_total > 0 or (quantidade > 0 and valor_unitario > 0)):
        return "item"

    if not has_financial:
        if item_stripped and _ITEM_NUM_GROUP.match(item_stripped):
            return "grupo"
        letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", desc)
        if letters and desc == desc.upper() and len(letters) >= 6:
            return "grupo"
        return "grupo"

    if any(kw in desc_norm for kw in _COMPOSICAO_KEYWORDS) and not is_xyz:
        return "composicao"

    if hint in {"composicao", "composição", "insumo", "subitem"} and not is_xyz:
        # Só respeita hint de composição se NÃO for numeração executiva
        return "composicao"

    if codigo and quantidade > 0:
        return "item"
    if quantidade > 0 and valor_unitario > 0:
        return "item"
    if valor_total > 0 and desc:
        return "item"

    return "composicao"


def is_executive_for_abc(item: dict[str, Any]) -> bool:
    tipo = str(item.get("tipo_linha") or item.get("tipo") or "item").lower()
    desc = str(item.get("descricao") or item.get("description") or "").lower()
    if tipo != "item":
        return False
    if "total do grupo" in desc:
        return False
    if item.get("quarentena") is True:
        return False
    if item.get("abc_elegivel") is False:
        return False
    vt = parse_brl(
        item.get("valor_total_com_bdi")
        if item.get("valor_total_com_bdi") is not None
        else item.get("valor_total")
    )
    return vt > 0


def line_total_com_bdi(item: dict[str, Any]) -> float:
    return parse_brl(
        item.get("valor_total_com_bdi")
        if item.get("valor_total_com_bdi") is not None
        else item.get("valor_total") or item.get("lineTotal") or item.get("totalValue")
    )


def enrich_item_pricing_and_type(raw: dict[str, Any]) -> dict[str, Any]:
    """Aplica contrato de preços + tipagem + flags de quarentena/ABC."""
    item = dict(raw)
    descricao = str(item.get("descricao") or item.get("description") or "").strip()
    codigo = str(item.get("codigo") or item.get("code") or "").strip()
    item_numero = str(item.get("item_numero") or item.get("item") or "").strip()
    tipo_hint = str(item.get("tipo_linha") or item.get("tipo") or "").strip()

    pricing = resolve_pricing_contract(
        quantidade=item.get("quantidade") or item.get("qty"),
        bdi=item.get("bdi") or item.get("BDI"),
        valor_unitario=item.get("valor_unitario") or item.get("unitPrice") or item.get("unitValue"),
        valor_total=item.get("valor_total") or item.get("totalValue") or item.get("lineTotal"),
        valor_unitario_sem_bdi=item.get("valor_unitario_sem_bdi"),
        valor_unitario_com_bdi=item.get("valor_unitario_com_bdi") or item.get("unit_com_bdi"),
        valor_total_sem_bdi=item.get("valor_total_sem_bdi") or item.get("preco_total_sem_bdi"),
        valor_total_com_bdi=item.get("valor_total_com_bdi") or item.get("total_com_bdi"),
    )

    tipo = infer_tipo_linha(
        descricao=descricao,
        quantidade=pricing["quantidade"],
        valor_unitario=pricing["valor_unitario_sem_bdi"],
        valor_total=pricing["valor_total_com_bdi"],
        codigo=codigo,
        item_numero=item_numero,
        tipo_hint=tipo_hint,
    )

    alerts = list(item.get("alertas") or [])
    for alert in pricing["alertas_preco"]:
        if alert not in alerts:
            alerts.append(alert)

    confianca = float(item.get("confianca") or 1.0)
    confianca = min(confianca, pricing["confianca_preco"])

    quarantine = bool(pricing["quarentena"])
    abc_eligible = (
        tipo == "item"
        and not quarantine
        and pricing["valor_total_com_bdi"] > 0
        and "total do grupo" not in descricao.lower()
    )

    item.update(
        {
            "descricao": descricao,
            "codigo": codigo,
            "item_numero": item_numero or str(item.get("item") or ""),
            "item": item_numero or str(item.get("item") or ""),
            "tipo": tipo,
            "tipo_linha": tipo,
            "quantidade": pricing["quantidade"],
            "bdi": pricing["bdi"],
            "valor_unitario_sem_bdi": pricing["valor_unitario_sem_bdi"],
            "valor_unitario_com_bdi": pricing["valor_unitario_com_bdi"],
            "valor_total_sem_bdi": pricing["valor_total_sem_bdi"],
            "valor_total_com_bdi": pricing["valor_total_com_bdi"],
            "valor_unitario": pricing["valor_unitario"],
            "valor_total": pricing["valor_total"],
            "quarentena": quarantine,
            "abc_elegivel": abc_eligible,
            "alertas": alerts,
            "confianca": round(confianca, 3),
            "confianca_preco": pricing["confianca_preco"],
        }
    )
    return item


def classify_abc_items(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Pareto 80/95: classificação pelo acumulado *antes* de incluir o item.
    Itens não elegíveis mantêm classification=None e ficam ao final.
    """
    enriched = [enrich_item_pricing_and_type(dict(i)) for i in items if isinstance(i, dict)]

    executives = [i for i in enriched if is_executive_for_abc(i)]
    others = [i for i in enriched if not is_executive_for_abc(i)]

    executives.sort(
        key=lambda i: (
            -line_total_com_bdi(i),
            str(i.get("item_numero") or i.get("codigo") or i.get("id") or ""),
        )
    )

    total = sum(line_total_com_bdi(i) for i in executives)
    accumulated = 0.0
    classified: list[dict[str, Any]] = []

    for item in executives:
        value = line_total_com_bdi(item)
        pct_before = (accumulated / total * 100.0) if total > 0 else 0.0
        accumulated += value
        pct_after = (accumulated / total * 100.0) if total > 0 else 0.0
        individual = (value / total * 100.0) if total > 0 else 0.0

        if pct_before < 80:
            cls = "A"
        elif pct_before < 95:
            cls = "B"
        else:
            cls = "C"

        item = dict(item)
        item["classification"] = cls
        item["individual_percentage"] = round(individual, 4)
        item["accumulated_percentage"] = round(pct_after, 4)
        item["lineTotal"] = round(value, 2)
        classified.append(item)

    for item in others:
        item = dict(item)
        item.setdefault("classification", None)
        item.setdefault("individual_percentage", 0.0)
        item.setdefault("accumulated_percentage", 0.0)
        item["lineTotal"] = round(line_total_com_bdi(item), 2)
        classified.append(item)

    return classified


def build_abc_summary(items: list[dict[str, Any]]) -> dict[str, Any]:
    executives = [i for i in items if is_executive_for_abc(i) and i.get("classification")]
    total = sum(line_total_com_bdi(i) for i in executives)
    quarantine_count = sum(1 for i in items if i.get("quarentena"))

    def _bucket(cls: str) -> dict[str, Any]:
        rows = [i for i in executives if i.get("classification") == cls]
        valor = sum(line_total_com_bdi(i) for i in rows)
        return {
            "count": len(rows),
            "valor": round(valor, 2),
            "percentual": round((valor / total * 100.0) if total > 0 else 0.0, 2),
        }

    return {
        "total_items": len(executives),
        "total_value": round(total, 2),
        "quarantine_count": quarantine_count,
        "class_a": _bucket("A"),
        "class_b": _bucket("B"),
        "class_c": _bucket("C"),
        "thresholds": {"a": 80, "b": 95},
        "algorithm": "pareto_before_item_80_95",
    }
