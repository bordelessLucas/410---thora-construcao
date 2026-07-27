"""Testes do parser BRL e da Curva ABC canônica."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.abc_curve import (  # noqa: E402
    build_abc_summary,
    classify_abc_items,
    enrich_item_pricing_and_type,
    infer_tipo_linha,
)
from app.domain.money import parse_brl, resolve_pricing_contract  # noqa: E402


def test_parse_brl_formats():
    assert parse_brl("1.234,56") == 1234.56
    assert parse_brl("1.234") == 1234.0
    assert parse_brl("12.34") == 12.34
    assert parse_brl("80,00") == 80.0
    assert parse_brl("R$ 51.138,33") == 51138.33
    assert parse_brl(21.22) == 21.22
    assert parse_brl("") == 0.0


def test_resolve_pricing_prefers_total_com_bdi():
    pricing = resolve_pricing_contract(
        quantidade="80,00",
        bdi="21,22",
        valor_unitario="528,47",  # s/BDI típico NOVACAP
        valor_total="51.138,33",  # c/BDI
    )
    assert abs(pricing["valor_total_com_bdi"] - 51138.33) < 0.02
    assert pricing["bdi"] > 0
    assert pricing["quarentena"] is False


def test_infer_tipo_xyz_is_item():
    tipo = infer_tipo_linha(
        descricao="ENGENHEIRO CIVIL",
        quantidade=6,
        valor_unitario=27456.32,
        valor_total=199695.30,
        codigo="93567",
        item_numero="2.1.1",
        tipo_hint="composicao",  # hint errado da IA
    )
    assert tipo == "item"


def test_abc_pareto_80_95():
    items = [
        {
            "item_numero": "1.1.1",
            "descricao": "Item dominante",
            "tipo_linha": "item",
            "quantidade": 1,
            "valor_unitario": 100,
            "valor_total": 850,
            "bdi": 0,
        },
        {
            "item_numero": "1.1.2",
            "descricao": "Item medio",
            "tipo_linha": "item",
            "quantidade": 1,
            "valor_unitario": 100,
            "valor_total": 100,
            "bdi": 0,
        },
        {
            "item_numero": "1.1.3",
            "descricao": "Item pequeno",
            "tipo_linha": "item",
            "quantidade": 1,
            "valor_unitario": 100,
            "valor_total": 50,
            "bdi": 0,
        },
    ]
    classified = classify_abc_items(items)
    by_num = {i["item_numero"]: i for i in classified if i.get("classification")}
    assert by_num["1.1.1"]["classification"] == "A"  # 0% before → A
    assert by_num["1.1.2"]["classification"] == "B"  # ~85% before → B
    assert by_num["1.1.3"]["classification"] == "C"  # ~95% before → C

    summary = build_abc_summary(classified)
    assert summary["total_items"] == 3
    assert abs(summary["total_value"] - 1000) < 0.01


def test_missing_prices_quarantine():
    enriched = enrich_item_pricing_and_type(
        {
            "item_numero": "1.2.3",
            "descricao": "Linha sem preços",
            "tipo_linha": "item",
            "quantidade": 0,
            "valor_unitario": 0,
            "valor_total": 0,
            "bdi": 0,
        }
    )
    assert enriched["quarentena"] is True
    assert enriched["abc_elegivel"] is False


def test_vt_prevails_without_quarantine():
    enriched = enrich_item_pricing_and_type(
        {
            "item_numero": "1.2.3",
            "descricao": "VU e VT divergentes",
            "tipo_linha": "item",
            "quantidade": 10,
            "valor_unitario": 100,
            "valor_total": 5000,
            "bdi": 0,
        }
    )
    assert enriched["valor_total_com_bdi"] == 5000.0
    assert enriched["quarentena"] is False
    assert enriched["abc_elegivel"] is True


def test_grupo_excluded_from_abc():
    classified = classify_abc_items(
        [
            {
                "item_numero": "1",
                "descricao": "ADMINISTRAÇÃO LOCAL",
                "tipo_linha": "grupo",
                "quantidade": 0,
                "valor_unitario": 0,
                "valor_total": 0,
            },
            {
                "item_numero": "1.1.1",
                "descricao": "Serviço",
                "tipo_linha": "item",
                "quantidade": 1,
                "valor_unitario": 100,
                "valor_total": 100,
                "bdi": 0,
            },
        ]
    )
    executives = [i for i in classified if i.get("classification")]
    assert len(executives) == 1
    assert executives[0]["classification"] == "A"


if __name__ == "__main__":
    test_parse_brl_formats()
    test_resolve_pricing_prefers_total_com_bdi()
    test_infer_tipo_xyz_is_item()
    test_abc_pareto_80_95()
    test_missing_prices_quarantine()
    test_vt_prevails_without_quarantine()
    test_grupo_excluded_from_abc()
    print("OK — money + abc_curve")
