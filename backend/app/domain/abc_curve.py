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
_ITEM_NUM_PREFIX = re.compile(r"^(\d+(?:\.\d+)*)")


def normalize_item_numero(value: Any) -> str:
    """Extrai numeração hierárquica limpa (ex.: '1.2 BOTA-FORA' → '1.2')."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = _ITEM_NUM_PREFIX.match(text)
    return match.group(1) if match else text


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
    item_stripped = normalize_item_numero(item_numero)
    codigo = (codigo or "").strip()
    hint = (tipo_hint or "").strip().lower()

    if any(kw in desc_norm for kw in _SUBTOTAL_KEYWORDS):
        return "grupo"

    has_financial = quantidade > 0 or valor_unitario > 0 or valor_total > 0
    is_xyz = bool(item_stripped and _ITEM_NUM_EXEC.match(item_stripped))
    is_group_num = bool(item_stripped and _ITEM_NUM_GROUP.match(item_stripped) and not is_xyz)

    # NOVACAP: serviço executivo padrão X.Y.Z
    if is_xyz and (valor_total > 0 or (quantidade > 0 and valor_unitario > 0)):
        return "item"

    # Planilha plana (001 / códigos simples) com Qtd × VU ≈ Total → item
    # Distingue de grupo sintético (qtd=1 e VU≈Total).
    raw_num = str(item_numero or "").strip()
    priced_line = (
        quantidade > 0
        and valor_unitario > 0
        and valor_total > 0
        and abs(quantidade * valor_unitario - valor_total) <= max(0.05, valor_total * 0.02)
    )
    if priced_line and (
        quantidade > 1
        or abs(valor_unitario - valor_total) > 0.02
        or (raw_num.isdigit() and raw_num.startswith("0"))
        or hint in {"item", "servico", "serviço", "folha"}
    ):
        return "item"

    # X ou X.Y sem código = cabeçalho de grupo (mesmo com total no sintético)
    if is_group_num and not codigo:
        return "grupo"

    # Código de catálogo (SINAPI/SICRO) com financeiro = serviço executivo
    # mesmo quando item_numero foi preenchido com o próprio código (Curva ABC pronta).
    catalog_code = bool(
        codigo
        and (
            re.fullmatch(r"\d{4,}(?:-\s*[A-Za-zÀ-ÿ0-9._/-]+)?", codigo)
            or re.fullmatch(r"[A-Za-z]{1,4}\s*\d{2,}(?:[.\-/]\d+)*", codigo)
        )
    )
    if catalog_code and (valor_total > 0 or (quantidade > 0 and valor_unitario > 0)):
        return "item"

    # Código que na verdade é descrição (linha de grupo com colunas desalinhadas)
    if is_group_num and codigo:
        codigo_limpo = codigo.strip()
        if (
            len(codigo_limpo) > 40
            or (codigo_limpo == item_stripped)
            or (" " in codigo_limpo and len(codigo_limpo) > 20)
            or codigo_limpo.upper() == desc[: len(codigo_limpo)].upper()
        ):
            return "grupo"

    if hint in {"grupo", "group", "titulo", "título"} and not codigo:
        return "grupo"

    if not has_financial:
        if is_group_num:
            return "grupo"
        letters = re.sub(r"[^A-Za-zÀ-ÿ]", "", desc)
        if letters and desc == desc.upper() and len(letters) >= 6:
            return "grupo"
        return "grupo"

    if any(kw in desc_norm for kw in _COMPOSICAO_KEYWORDS) and not is_xyz:
        return "composicao"

    if hint in {"composicao", "composição", "insumo", "subitem"} and not is_xyz:
        return "composicao"

    if not item_stripped and not is_xyz:
        tipo_cell = hint or ""
        if any(k in tipo_cell for k in ("compos", "insumo", "auxiliar")):
            return "composicao"

    # Serviço executivo: X.Y.Z ou X.Y/X com código de catálogo + financeiro (folhas 4.1, 7.1, 6.4…)
    codigo_ok = bool(codigo) and not (
        len(codigo) > 40
        or (" " in codigo and len(codigo) > 20)
        or codigo == item_stripped
    )
    if codigo_ok and (valor_total > 0 or (quantidade > 0 and valor_unitario > 0)):
        return "item"
    if is_xyz and (valor_total > 0 or (quantidade > 0 and valor_unitario > 0)):
        return "item"
    # Curva ABC: tipagem explícita "item" + financeiro prevalece sobre nº sequencial 1..N
    if hint in {"item", "servico", "serviço", ""} and has_financial:
        return "item"
    if is_group_num and has_financial and hint == "item":
        return "item"
    if is_group_num:
        return "grupo"

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


def _item_numero_of(item: dict[str, Any]) -> str:
    return normalize_item_numero(item.get("item_numero") or item.get("item") or "")


def drop_non_leaf_executives(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Remove pais hierárquicos quando há filhos na mesma lista (evita 1.2 + 1.2.1).

    Mantém serviço precificado (qtd×VU) mesmo com filho fantasma por numeração
    inconsistente no PDF (ex.: 3.1.1 e 3.1.1.1 em seções diferentes).
    """
    numbers = {_item_numero_of(i) for i in items if _item_numero_of(i)}
    out: list[dict[str, Any]] = []
    for item in items:
        num = _item_numero_of(item)
        has_child = bool(num) and any(other.startswith(num + ".") for other in numbers)
        if has_child:
            qty = float(item.get("quantidade") or 0)
            vu = float(
                item.get("valor_unitario_com_bdi")
                or item.get("valor_unitario")
                or 0
            )
            codigo = str(item.get("codigo") or "").strip()
            total = line_total_com_bdi(item)
            priced = qty > 0 and vu > 0 and (codigo or abs(vu - total) > 0.02)
            if not priced:
                continue
        out.append(item)
    return out


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
    raw_item_numero = str(item.get("item_numero") or item.get("item") or "").strip()
    item_numero = normalize_item_numero(raw_item_numero)
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
        valor_unitario=pricing["valor_unitario_com_bdi"] or pricing["valor_unitario_sem_bdi"],
        valor_total=pricing["valor_total_com_bdi"],
        codigo=codigo,
        item_numero=raw_item_numero or item_numero,
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
    Pareto 80/95: classificação pelo acumulado *depois* de incluir o item.

    A: acumulado <= 80%
    B: 80% < acumulado <= 95%
    C: acumulado > 95%

    Alinhado a Curvas ABC de serviços em PDF (FAIXA do documento).
    """
    enriched = [enrich_item_pricing_and_type(dict(i)) for i in items if isinstance(i, dict)]

    executives = [i for i in enriched if is_executive_for_abc(i)]
    executives = drop_non_leaf_executives(executives)
    executive_ids = {id(i) for i in executives}
    others = [i for i in enriched if id(i) not in executive_ids]

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
        accumulated += value
        pct_after = (accumulated / total * 100.0) if total > 0 else 0.0
        individual = (value / total * 100.0) if total > 0 else 0.0

        if pct_after <= 80:
            cls = "A"
        elif pct_after <= 95:
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
        "algorithm": "pareto_after_item_80_95",
    }
