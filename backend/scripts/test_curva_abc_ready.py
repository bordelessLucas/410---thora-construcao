"""
Testes: Curva ABC já pronta no PDF (headers CUSTO PARCIAL / % INCID / FAIXA).

Fixture sintética baseada no caso Fábrica Social (~R$ 7.358.765,08).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from budget_parser import BudgetParser  # noqa: E402
from app.domain.abc_curve import build_abc_summary, classify_abc_items  # noqa: E402
from app.domain.money import parse_brl  # noqa: E402
from app.domain.profiles import match_profile, parse_rows_with_profile  # noqa: E402
from app.domain.profiles.adapters import parse_curva_abc  # noqa: E402
from app.domain.services.orcamento_extraction import (  # noqa: E402
    _attach_abc_document_divergences,
    _build_finance_validation_from_items,
    _diagnose_abc_row_parse,
    _is_abc_ready_profile,
)

TOL = 0.05
EXPECTED_TOTAL = 7_358_765.08


def _fmt_brl(value: float) -> str:
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _fmt_pct(value: float) -> str:
    return f"{value:.3f}".replace(".", ",") + "%"


def build_fabrica_social_abc_rows() -> list[list[str]]:
    """
    Matriz 9 colunas (~29 serviços) com totais/percentuais do caso de referência.
    Valores 3..29 distribuídos para fechar o total geral.
    """
    header = [
        "CÓDIGO",
        "DESCRIÇÃO",
        "UNID",
        "QUANTIDADE",
        "CUSTO UNIT",
        "CUSTO PARCIAL",
        "% INCID",
        "% ACUMUL",
        "FAIXA",
    ]
    # (codigo, desc, unid, qtd, vu, parcial, incid, acumul, faixa)
    first = (
        "93370",
        "SERVIÇO PRINCIPAL DE REFERÊNCIA — ITEM A1",
        "M3",
        "1.425.600,18",
        "1,49",
        "2.124.144,27",
        "28,865%",
        "28,865%",
        "A",
    )
    second = (
        "92394-ADAPTADO",
        "SERVIÇO SECUNDÁRIO DE REFERÊNCIA — ITEM A2",
        "M2",
        "50.000,00",
        "22,65",
        "1.132.500,00",
        "15,390%",
        "44,255%",
        "A",
    )

    remaining_total = EXPECTED_TOTAL - 2_124_144.27 - 1_132_500.00
    n_rest = 27
    base = round(remaining_total / n_rest, 2)
    parts = [base] * (n_rest - 1)
    parts.append(round(remaining_total - sum(parts), 2))

    rows: list[list[str]] = [header, list(first), list(second)]
    acum = 28.865 + 15.390
    for i, parcial in enumerate(parts, start=3):
        incid = parcial / EXPECTED_TOTAL * 100.0
        acum += incid
        faixa = "A" if acum <= 80 else ("B" if acum <= 95 else "C")
        codigo = f"{90000 + i}"
        if i == 5:
            # Código partido (simula PDF quebrado) — ainda na mesma célula lógica
            codigo = "87464-ADAPTADO"
        rows.append(
            [
                codigo,
                f"SERVIÇO DE TESTE NÚMERO {i:02d} PARA CURVA ABC",
                "UN",
                "1,00",
                _fmt_brl(parcial),
                _fmt_brl(parcial),
                _fmt_pct(incid),
                _fmt_pct(acum),
                faixa,
            ]
        )
    return rows


def test_sanitize_qty_as_total():
    from budget_parser import BudgetParser

    q, vu, vt = BudgetParser.sanitize_abc_economics(100_000.0, 0.48, 100_000.0)
    assert abs(vt - 48_000.0) < 0.01
    assert abs(vu - 0.48) < 1e-9

    q2, vu2, vt2 = BudgetParser.sanitize_abc_economics(50_000.0, 9.20, 50_000.0)
    assert abs(vt2 - 460_000.0) < 0.01


def test_parse_brl_abc_values():
    assert abs(parse_brl("1.425.600,18") - 1_425_600.18) < 1e-6
    assert abs(parse_brl("2.124.144,27") - 2_124_144.27) < 1e-6
    assert abs(parse_brl("28,865%") - 28.865) < 1e-6
    assert abs(parse_brl("7.358.765,08") - EXPECTED_TOTAL) < 1e-6


def test_header_unid_not_valor_unitario():
    from budget_parser import BudgetParser

    p = BudgetParser()
    st = p.identify_columns(
        ["CÓDIGO", "DESCRIÇÃO", "UNID", "QUANTIDADE", "CUSTO UNIT", "CUSTO PARCIAL", "% INCID", "% ACUMUL", "FAIXA"]
    )
    assert st["unidade"] == 2
    assert st["valor_unitario"] == 4
    assert st["valor_total"] == 5
    assert st["quantidade"] == 3



def test_header_aliases_map_custo_parcial():
    parser = BudgetParser()
    header = [
        "CÓDIGO",
        "DESCRIÇÃO",
        "UNID",
        "QUANTIDADE",
        "CUSTO UNIT",
        "CUSTO PARCIAL",
        "% INCID",
        "% ACUMUL",
        "FAIXA",
    ]
    assert parser.is_abc_header_row(header)
    structure = parser.identify_columns(header)
    assert structure["codigo"] == 0
    assert structure["descricao"] == 1
    assert structure["unidade"] == 2
    assert structure["quantidade"] == 3
    assert structure["valor_unitario"] == 4
    assert structure["valor_total"] == 5
    assert structure["pct_incid"] == 6
    assert structure["pct_acumul"] == 7
    assert structure["faixa"] == 8


def test_profile_match_curva_abc():
    rows = build_fabrica_social_abc_rows()
    match = match_profile(rows, table_name="CURVA ABC DE SERVIÇOS")
    assert match.profile_id == "curva_abc"
    assert match.confidence >= 0.4
    assert _is_abc_ready_profile(match.profile_id, rows, "CURVA ABC DE SERVIÇOS")


def test_extract_approx_29_items_and_totals():
    rows = build_fabrica_social_abc_rows()
    items, diag = _diagnose_abc_row_parse(
        rows, page=1, table_id="t1", profile_id="curva_abc"
    )
    assert diag["accepted_items"] > 0
    assert len(items) >= 29
    assert len(items) <= 34

    classified = classify_abc_items(items)
    classified = _attach_abc_document_divergences(classified)
    executives = [i for i in classified if i.get("classification")]
    assert len(executives) >= 29

    summary = build_abc_summary(classified)
    total = float(summary.get("total_value") or 0)
    assert abs(total - EXPECTED_TOTAL) <= 1.0, f"total={total}"

    # Ordenados por valor desc
    executives_sorted = sorted(
        executives,
        key=lambda i: -float(i.get("valor_total_com_bdi") or i.get("valor_total") or 0),
    )
    first = executives_sorted[0]
    second = executives_sorted[1]
    assert abs(float(first.get("lineTotal") or first.get("valor_total") or 0) - 2_124_144.27) <= TOL
    assert abs(float(first.get("individual_percentage") or 0) - 28.865) <= 0.05
    assert abs(float(first.get("accumulated_percentage") or 0) - 28.865) <= 0.05
    assert abs(float(second.get("accumulated_percentage") or 0) - 44.255) <= 0.08

    last = executives_sorted[-1]
    assert abs(float(last.get("accumulated_percentage") or 0) - 100.0) <= 0.15
    assert any(i.get("classification") in {"A", "B", "C"} for i in executives)
    assert any(i.get("doc_faixa") for i in items)

    finance = _build_finance_validation_from_items(classified)
    assert finance["ok"] is True
    assert abs(float(finance["total_geral"]["soma_folhas"]) - EXPECTED_TOTAL) <= 1.0


def test_art_crea_faixa_not_treated_as_header():
    """
    ART DE OBRA… FAIXA 2… não pode ser is_header_row (serviço+und+faixa = 3 hits).
    """
    parser = BudgetParser()
    header = [
        "CÓDIGO",
        "DESCRIÇÃO",
        "UNID",
        "QUANTIDADE",
        "CUSTO UNIT",
        "CUSTO PARCIAL",
        "% INCID",
        "% ACUMUL",
        "FAIXA",
    ]
    art_row = [
        "CREA/DF",
        "ART DE OBRA OU SERVIÇO - FAIXA 2 - CONTRATO ACIMA DE R$ 15.000,00 - 2026",
        "UND",
        "20,00",
        "262,55",
        "5.251,00",
        "0,071%",
        "99,929%",
        "C",
    ]
    assert parser.is_header_row(header) is True
    assert parser.is_header_row(art_row) is False

    rows = [
        header,
        [
            "90001",
            "DESLOCAMENTO ENTRE FUROS",
            "UN",
            "1,00",
            "100,00",
            "100,00",
            "1,000%",
            "1,000%",
            "C",
        ],
        art_row,
        ["", "REMOÇÃO DE PLACA DE SINALIZAÇÃO", "UN", "1,00", "3.117,00", "3.117,00", "0,042%", "99,971%", "C"],
        ["", "TRANSPORTE COM MUNCK", "UN", "1,00", "62,33", "62,33", "0,001%", "100,000%", "C"],
    ]
    items, _ = parser.parse_table(rows, page=1)
    assert len(items) == 4, f"esperava 4, veio {len(items)}: {[i.get('descricao') for i in items]}"
    assert any(abs(float(i.get("valor_total") or 0) - 5251.0) < 0.05 for i in items)
    art = next(i for i in items if abs(float(i.get("valor_total") or 0) - 5251.0) < 0.05)
    assert "ART" in str(art.get("descricao") or "").upper()
    assert abs(float(art.get("quantidade") or 0) - 20.0) < 0.01
    assert abs(sum(float(i.get("valor_total") or 0) for i in items) - (100 + 5251 + 3117 + 62.33)) < 0.05


def test_drop_non_leaf_excludes_hierarchical_groups():
    """Grupo 10 + filhos 10.1/10.2 não pode somar o pai na ABC."""
    from app.domain.abc_curve import classify_abc_items, is_executive_for_abc, line_total_com_bdi

    raw = [
        {
            "item_numero": "10",
            "codigo": "",
            "descricao": "Execução de pavimentação intertravada",
            "quantidade": 1.0,
            "valor_unitario": 5_388_872.56,
            "valor_total": 5_388_872.56,
            "valor_total_com_bdi": 5_388_872.56,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
        {
            "item_numero": "10.1",
            "codigo": "92398",
            "descricao": "EXECUÇÃO DE PAVIMENTO EM PISO INTERTRAVADO",
            "quantidade": 33874.39,
            "valor_unitario": 143.84,
            "valor_total": 4_872_492.25,
            "valor_total_com_bdi": 4_872_492.25,
            "tipo_linha": "item",
            "tipo": "item",
        },
        {
            "item_numero": "10.2",
            "codigo": "",
            "descricao": "Transporte do bloco de concreto",
            "quantidade": 1.0,
            "valor_unitario": 346_948.17,
            "valor_total": 346_948.17,
            "valor_total_com_bdi": 346_948.17,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
        {
            "item_numero": "10.2.1",
            "codigo": "100992",
            "descricao": "CARGA, MANOBRA E DESCARGA",
            "quantidade": 6596.6,
            "valor_unitario": 6.95,
            "valor_total": 45_846.37,
            "valor_total_com_bdi": 45_846.37,
            "tipo_linha": "item",
            "tipo": "item",
        },
        {
            "item_numero": "12.3",
            "codigo": "",
            "descricao": "Execução de calçada e rampa",
            "quantidade": 1.0,
            "valor_unitario": 304_056.41,
            "valor_total": 304_056.41,
            "valor_total_com_bdi": 304_056.41,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
        {
            "item_numero": "12.3.1",
            "codigo": "94991M",
            "descricao": "EXECUÇÃO DE PASSEIO",
            "quantidade": 338.74,
            "valor_unitario": 897.61,
            "valor_total": 304_056.41,
            "valor_total_com_bdi": 304_056.41,
            "tipo_linha": "item",
            "tipo": "item",
        },
    ]
    classified = classify_abc_items(raw)
    with_class = [i for i in classified if i.get("classification") in {"A", "B", "C"}]
    nums = {str(i.get("item_numero")) for i in with_class}
    assert "10" not in nums
    assert "10.2" not in nums
    assert "12.3" not in nums
    assert "10.1" in nums
    assert "10.2.1" in nums
    assert "12.3.1" in nums
    soma = sum(line_total_com_bdi(i) for i in with_class)
    # Sem dupla contagem dos pais
    assert abs(soma - (4_872_492.25 + 45_846.37 + 304_056.41)) < 0.05
    assert all(is_executive_for_abc(i) or i.get("classification") for i in with_class)


def test_does_not_merge_trailing_abc_services():
    """Últimos serviços (mesmo Title Case) não podem fundir na descrição."""
    parser = BudgetParser()
    header = [
        "CÓDIGO",
        "DESCRIÇÃO",
        "UNID",
        "QUANTIDADE",
        "CUSTO UNIT",
        "CUSTO PARCIAL",
        "% INCID",
        "% ACUMUL",
        "FAIXA",
    ]
    rows = [
        header,
        [
            "90001",
            "DESLOCAMENTO, ENTRE FUROS, DE EQUIPAMENTOS DE SONDAGEM",
            "UN",
            "1,00",
            "100,00",
            "100,00",
            "1,000%",
            "1,000%",
            "C",
        ],
        # Title Case — antes podia fundir; agora é novo serviço
        ["", "Art de obra/serviço", "", "", "", "", "", "", ""],
        ["", "", "UN", "1,00", "5.251,00", "5.251,00", "0,071%", "99,900%", "C"],
        ["", "REMOÇÃO DE PLACA DE SINALIZAÇÃO", "", "", "", "", "", "", ""],
        ["", "", "UN", "1,00", "3.117,00", "3.117,00", "0,042%", "99,950%", "C"],
        ["", "Transporte com Munck", "", "", "", "", "", "", ""],
        ["", "", "UN", "1,00", "62,33", "62,33", "0,001%", "100,000%", "C"],
    ]
    items, _ = parser.parse_table(rows, page=1)
    totals = sorted(float(i.get("valor_total") or 0) for i in items)
    assert len(items) == 4, f"esperava 4 itens, veio {len(items)}: {[i.get('descricao') for i in items]}"
    assert abs(sum(totals) - (100.0 + 5_251.0 + 3_117.0 + 62.33)) <= 0.05
    assert any(abs(float(i.get("valor_total") or 0) - 5251.0) < 0.05 for i in items)
    assert any(abs(float(i.get("valor_total") or 0) - 3117.0) < 0.05 for i in items)
    assert any(abs(float(i.get("valor_total") or 0) - 62.33) < 0.05 for i in items)


def test_realign_numeric_description_descarte():
    """Quantidade na coluna descrição (Taxa de descarte / RCC)."""
    parser = BudgetParser()
    header = [
        "CÓDIGO",
        "DESCRIÇÃO",
        "UNID",
        "QUANTIDADE",
        "CUSTO UNIT",
        "CUSTO PARCIAL",
        "% INCID",
        "% ACUMUL",
        "FAIXA",
    ]
    rows = [
        header,
        [
            "Taxa de descarte de resíduos da construção civil e volumes - RCC",
            "7.501,20",
            "T",
            "7.501,20",
            "17,13",
            "128.495,56",
            "1,746%",
            "90,000%",
            "B",
        ],
    ]
    items, _ = parser.parse_table(rows, page=1)
    assert len(items) == 1
    item = items[0]
    assert "descarte" in str(item.get("descricao") or "").lower()
    assert not BudgetParser()._looks_like_pure_number_cell(str(item.get("descricao") or ""))
    assert abs(float(item.get("quantidade") or 0) - 7501.20) < 0.05
    assert abs(float(item.get("valor_total") or 0) - 128_495.56) < 0.05
    assert str(item.get("unidade") or "").upper() in {"T", "TB"}


def test_abc_items_without_catalog_code_stay_executive():
    from app.domain.abc_curve import classify_abc_items, is_executive_for_abc

    raw = [
        {
            "item_numero": "27",
            "item": "27",
            "codigo": "",
            "descricao": "REMOÇÃO DE PLACA DE SINALIZAÇÃO",
            "quantidade": 1.0,
            "unidade": "UN",
            "valor_unitario": 3117.0,
            "valor_total": 3117.0,
            "valor_total_com_bdi": 3117.0,
            "tipo_linha": "item",
            "tipo": "item",
        },
        {
            "item_numero": "28",
            "item": "28",
            "codigo": "",
            "descricao": "TRANSPORTE COM MUNCK",
            "quantidade": 1.0,
            "unidade": "UN",
            "valor_unitario": 62.33,
            "valor_total": 62.33,
            "valor_total_com_bdi": 62.33,
            "tipo_linha": "item",
            "tipo": "item",
        },
    ]
    classified = classify_abc_items(raw)
    assert len(classified) == 2
    assert all(is_executive_for_abc(i) for i in classified)
    assert all(i.get("classification") in {"A", "B", "C"} for i in classified)


def test_priced_grupo_leaves_in_abc_and_xlsx_export():
    """
    Folhas tipadas como grupo (7.5 / 7.6 / 16.1 sem SINAPI) devem entrar na ABC
    e no prepare_curva_abc_rows — mesmo filtro dos cards (86 folhas).
    """
    from app.domain.abc_curve import classify_abc_items, enrich_item_pricing_and_type
    from services.xlsx_export import prepare_curva_abc_rows

    raw = [
        {
            "item_numero": "1.1.1",
            "item": "1.1.1",
            "codigo": "90001",
            "descricao": "Servico A dominante",
            "quantidade": 1.0,
            "unidade": "UN",
            "valor_unitario": 8_000_000.0,
            "valor_total": 8_000_000.0,
            "valor_total_com_bdi": 8_000_000.0,
            "tipo_linha": "item",
            "tipo": "item",
        },
        {
            "item_numero": "16.1",
            "item": "16.1",
            "codigo": "",
            "descricao": "Poco de Visita PVI 02",
            "quantidade": 1.0,
            "unidade": "UN",
            "valor_unitario": 111_780.30,
            "valor_total": 111_780.30,
            "valor_total_com_bdi": 111_780.30,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
        {
            "item_numero": "7.5",
            "item": "7.5",
            "codigo": "",
            "descricao": "Disposicao final de residuos segregados",
            "quantidade": 1.0,
            "unidade": "M3",
            "valor_unitario": 7_991.90,
            "valor_total": 7_991.90,
            "valor_total_com_bdi": 7_991.90,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
        {
            "item_numero": "7.6",
            "item": "7.6",
            "codigo": "",
            "descricao": "Disposicao final de residuos nao segregados",
            "quantidade": 1.0,
            "unidade": "M3",
            "valor_unitario": 5_682.59,
            "valor_total": 5_682.59,
            "valor_total_com_bdi": 5_682.59,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
        # Pai agregador — fora da ABC
        {
            "item_numero": "7",
            "item": "7",
            "codigo": "",
            "descricao": "RESIDUOS",
            "quantidade": 1.0,
            "unidade": "",
            "valor_unitario": 13_674.49,
            "valor_total": 13_674.49,
            "valor_total_com_bdi": 13_674.49,
            "tipo_linha": "grupo",
            "tipo": "grupo",
        },
    ]

    for leaf in ("16.1", "7.5", "7.6"):
        enriched = enrich_item_pricing_and_type(
            next(i for i in raw if i["item_numero"] == leaf)
        )
        assert enriched["abc_elegivel"] is True, leaf

    classified = [i for i in classify_abc_items(raw) if i.get("classification")]
    nums = {str(i.get("item_numero")) for i in classified}
    assert nums == {"1.1.1", "16.1", "7.5", "7.6"}
    total_cls = sum(float(i.get("valor_total_com_bdi") or 0) for i in classified)
    assert abs(total_cls - 8_125_454.79) < 0.05

    rows, total = prepare_curva_abc_rows(raw)
    assert len(rows) == 4
    assert abs(total - 8_125_454.79) < 0.05
    export_nums = {str(r.get("item_numero")) for r in rows}
    assert export_nums == {"1.1.1", "16.1", "7.5", "7.6"}

    # % individual e acumulado no mesmo universo
    top = rows[0]
    assert abs(top["percent"] - top["accumulated"]) < 0.0001
    assert abs(top["percent"] - (8_000_000.0 / total * 100)) < 0.01


def test_resolve_qty_vu_four_column_layout():
    """qtd + VU s/BDI + VU c/BDI + total — não promover VU para quantidade."""
    from app.domain.budget_pipeline.engine2_table import _resolve_qty_vu_from_moneys

    q, vu, vt = _resolve_qty_vu_from_moneys(
        [33_874.39, 115.27, 143.84, 4_872_492.25]
    )
    assert abs(q - 33_874.39) < 0.01, q
    assert abs(vu - 115.27) < 0.01, vu
    assert abs(vt - 4_872_492.25) < 0.05, vt

    q2, vu2, vt2 = _resolve_qty_vu_from_moneys([30.0, 2_985.83, 3_726.01, 111_780.30])
    assert abs(q2 - 30.0) < 0.01, q2
    assert abs(vu2 - 2_985.83) < 0.01, vu2
    assert abs(vt2 - 111_780.30) < 0.05, vt2

    q3, vu3, vt3 = _resolve_qty_vu_from_moneys([12_119.0, 51.43, 64.17, 777_676.23])
    assert abs(q3 - 12_119.0) < 0.01, q3
    assert abs(vu3 - 51.43) < 0.01, vu3


def test_zero_qty_zero_total_not_invented():
    """qty=0 + total=0 + VU>0 → preservar zeros (não inventar 1×VU)."""
    from app.domain.abc_curve import classify_abc_items, is_executive_for_abc
    from app.domain.budget_pipeline.engine2_table import _hit_to_row, _resolve_qty_vu_from_moneys
    from app.domain.money import resolve_pricing_contract

    q, vu, vt = _resolve_qty_vu_from_moneys([0.0, 0.55, 0.68, 0.0])
    assert q == 0.0
    assert abs(vu - 0.55) < 0.01 or abs(vu - 0.68) < 0.01
    assert vt == 0.0

    pricing = resolve_pricing_contract(
        quantidade=0,
        valor_unitario=0.55,
        valor_unitario_com_bdi=0.68,
        valor_total=0,
        valor_total_com_bdi=0,
        bdi=24.79,
    )
    assert pricing["quantidade"] == 0.0
    assert pricing["valor_total_com_bdi"] == 0.0

    row = _hit_to_row(
        {
            "item_numero": "10.2.3",
            "codigo": "95430",
            "descricao": "Servico zerado",
            "unidade": "m2",
            "quantidade": 0.0,
            "valor_unitario": 0.55,
            "valor_total": 0.0,
            "bdi": 24.79,
        },
        ["10.2.3", "95430", "Servico zerado", "m2", "0", "0,55", "0,68", "0,00"],
        page=1,
    )
    assert row.quantidade == 0.0
    assert row.valor_total == 0.0

    classified = classify_abc_items(
        [
            {
                "item_numero": "10.2.3",
                "codigo": "95430",
                "descricao": "Servico zerado",
                "quantidade": 0,
                "valor_unitario": 0.55,
                "valor_unitario_com_bdi": 0.68,
                "valor_total": 0,
                "valor_total_com_bdi": 0,
                "tipo_linha": "item",
            },
            {
                "item_numero": "10.1",
                "codigo": "92398",
                "descricao": "Pavimento",
                "quantidade": 100,
                "valor_unitario": 10,
                "valor_total": 1000,
                "valor_total_com_bdi": 1000,
                "tipo_linha": "item",
            },
        ]
    )
    execs = [i for i in classified if i.get("classification")]
    assert len(execs) == 1
    assert execs[0]["item_numero"] == "10.1"
    assert not is_executive_for_abc(classified[0]) or classified[0].get("item_numero") == "10.1"


def test_parse_14_1_integer_qty_after_unit():
    """12119 após unidade M não pode ser descartado como código SINAPI."""
    from app.domain.budget_pipeline.engine2_table import _parse_semantic_row

    cells = [
        "14.1",
        "SINAPI",
        "94273",
        "SERVICO EXEMPLO",
        "M",
        "12119",
        "51,43",
        "64,17",
        "777.676,23",
    ]
    row = _parse_semantic_row(cells, 1)
    assert row is not None
    assert abs(row.quantidade - 12_119.0) < 0.01, row.quantidade
    assert abs(row.valor_unitario - 51.43) < 0.01, row.valor_unitario
    assert abs(row.valor_total - 777_676.23) < 0.05, row.valor_total


def test_xlsx_abc_preserves_qty_and_unit_com_bdi():
    from services.xlsx_export import prepare_curva_abc_rows

    raw = [
        {
            "item_numero": "10.1",
            "item": "10.1",
            "codigo": "92398",
            "descricao": "EXECUÇÃO DE PAVIMENTO EM PISO INTERTRAVADO",
            "unidade": "m2",
            "quantidade": 33_874.39,
            "qty": 33_874.39,
            "valor_unitario": 115.27,
            "valor_unitario_sem_bdi": 115.27,
            "valor_unitario_com_bdi": 143.84,
            "valor_total": 4_872_492.25,
            "valor_total_com_bdi": 4_872_492.25,
            "bdi": 24.79,
            "tipo_linha": "item",
            "tipo": "item",
        }
    ]
    rows, total = prepare_curva_abc_rows(raw)
    assert len(rows) == 1
    row = rows[0]
    assert abs(row["qty"] - 33_874.39) < 0.01, row["qty"]
    assert abs(row["unit_com_bdi"] - 143.84) < 0.02, row["unit_com_bdi"]
    assert abs(row["bdi"] - 24.79) < 0.05, row["bdi"]
    assert abs(row["total_com_bdi"] - 4_872_492.25) < 0.05
    assert abs(total - 4_872_492.25) < 0.05


def test_parse_curva_abc_adapter_sequential_item_numero():
    rows = build_fabrica_social_abc_rows()
    items = parse_curva_abc(rows, page=1)
    assert len(items) >= 29
    # Não deve copiar código SINAPI para item_numero (vira "grupo" no front)
    for item in items:
        codigo = str(item.get("codigo") or "")
        item_num = str(item.get("item_numero") or "")
        if codigo and re.fullmatch(r"\d{4,}", codigo):
            assert item_num != codigo


def test_parse_curva_abc_adapter_preserves_doc_fields():
    rows = build_fabrica_social_abc_rows()
    items = parse_curva_abc(rows, page=1)
    assert len(items) >= 29
    top = max(items, key=lambda i: float(i.get("valor_total") or 0))
    assert abs(float(top["valor_total"]) - 2_124_144.27) <= TOL
    assert top.get("doc_percentual") is not None or top.get("doc_faixa")


def test_fragmented_code_merge():
    parser = BudgetParser()
    rows = [
        [
            "CÓDIGO",
            "DESCRIÇÃO",
            "UNID",
            "QUANTIDADE",
            "CUSTO UNIT",
            "CUSTO PARCIAL",
            "% INCID",
            "% ACUMUL",
            "FAIXA",
        ],
        ["92394-", "SERVICO PARTE 1", "UN", "1,00", "10,00", "10,00", "50,000%", "50,000%", "A"],
        ["ADAPTADO", "CONTINUACAO CODIGO", "UN", "0", "0", "0", "", "", ""],
        ["90001", "OUTRO SERVICO", "UN", "1,00", "10,00", "10,00", "50,000%", "100,000%", "A"],
    ]
    items, _ = parser.parse_table(rows, page=1)
    codes = [str(i.get("codigo") or "") for i in items]
    assert any("92394-ADAPTADO" in c or c.startswith("92394") for c in codes)
    assert len(items) >= 2


def test_parse_rows_with_profile_forced():
    rows = build_fabrica_social_abc_rows()
    items, match = parse_rows_with_profile(rows, page=1, profile_id="curva_abc")
    assert match.profile_id == "curva_abc"
    assert len(items) >= 29


def main() -> None:
    tests = [
        test_sanitize_qty_as_total,
        test_header_unid_not_valor_unitario,
        test_parse_brl_abc_values,
        test_header_aliases_map_custo_parcial,
        test_profile_match_curva_abc,
        test_extract_approx_29_items_and_totals,
        test_art_crea_faixa_not_treated_as_header,
        test_drop_non_leaf_excludes_hierarchical_groups,
        test_does_not_merge_trailing_abc_services,
        test_realign_numeric_description_descarte,
        test_abc_items_without_catalog_code_stay_executive,
        test_priced_grupo_leaves_in_abc_and_xlsx_export,
        test_resolve_qty_vu_four_column_layout,
        test_zero_qty_zero_total_not_invented,
        test_parse_14_1_integer_qty_after_unit,
        test_xlsx_abc_preserves_qty_and_unit_com_bdi,
        test_parse_curva_abc_adapter_sequential_item_numero,
        test_parse_curva_abc_adapter_preserves_doc_fields,
        test_fragmented_code_merge,
        test_parse_rows_with_profile_forced,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"OK  {fn.__name__}")
        except Exception as exc:
            failed += 1
            print(f"FAIL {fn.__name__}: {exc}")
            raise
    print(f"\n{len(tests) - failed}/{len(tests)} passed")


if __name__ == "__main__":
    main()
