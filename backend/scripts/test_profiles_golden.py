"""
Golden: registry de perfis + Orçamento Sintético NOVACAP = R$ 9.055.082,16.

Uso:
  cd backend && uv run python scripts/test_profiles_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.abc_curve import build_abc_summary, classify_abc_items  # noqa: E402
from app.domain.profiles import match_profile  # noqa: E402
from app.domain.services.orcamento_extraction import (  # noqa: E402
    _deduplicate_items,
    _filter_for_analysis,
    _items_from_table_rows,
    classify_table_kind,
)

GOLDEN_TOTAL = 9_055_082.16
GOLDEN_PDF = ROOT / "test_pdfs" / "planilha_orcamentaria_analise.pdf"
TOLERANCE = 1.0


def brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def test_profile_matching_samples() -> None:
    sintetico_rows = [
        ["ORÇAMENTO SINTÉTICO"],
        ["Item", "Descrição", "Unidade", "Quantidade", "Valor Unit.", "Total", "Peso (%)"],
        ["1.1.1", "Serviço A", "m2", "10,00", "100,00", "1.000,00", "5,00 %"],
        ["1.1.2", "Serviço B", "m2", "20,00", "50,00", "1.000,00", "5,00 %"],
        ["1.1.3", "Serviço C", "un", "1,00", "500,00", "500,00", "2,50 %"],
    ]
    planilha_rows = [
        [
            "Item",
            "Fonte",
            "Código",
            "Descrição",
            "Unid.",
            "Qtde.",
            "Preço Unit.",
            "Preço Total",
            "BDI (%)",
            "Total c/ BDI",
        ],
        [
            "1.1.1",
            "SINAPI",
            "12345",
            "Serviço",
            "m2",
            "10,00",
            "100,00",
            "1.000,00",
            "21,22",
            "1.212,20",
        ],
    ]
    composicao_rows = [
        ["COMPOSIÇÃO DE CUSTOS UNITÁRIOS"],
        ["Tipo", "Código", "Descrição", "Unidade", "Coeficiente", "Preço"],
        ["Insumo", "001", "Cimento", "kg", "1,0000000", "0,85"],
        ["Insumo", "002", "Areia", "m3", "0,0034000", "120,00"],
        ["Insumo", "003", "Brita", "m3", "0,0006000", "95,00"],
    ]

    cases = [
        (sintetico_rows, "Orçamento Sintético", "novacap_sintetico", "sintetico"),
        (planilha_rows, "Planilha Orçamentária", "novacap_planilha", "orcamento"),
        (composicao_rows, "Composição", "composicao_unitaria", "composicao"),
    ]

    for rows, name, expected_id, expected_kind in cases:
        match = match_profile(rows, table_name=name)
        kind = classify_table_kind(rows, table_name=name)
        print(
            f"  sample '{name}': profile={match.profile_id} "
            f"kind={kind} conf={match.confidence:.2f} reasons={match.reasons}"
        )
        assert match.profile_id == expected_id, (
            f"{name}: esperado perfil {expected_id}, got {match.profile_id}"
        )
        assert kind == expected_kind, f"{name}: esperado kind {expected_kind}, got {kind}"


def test_golden_pdf_sintetico() -> None:
    if not GOLDEN_PDF.exists():
        raise FileNotFoundError(f"PDF golden ausente: {GOLDEN_PDF}")

    from app.infrastructure.pdf.table_extract import (
        extract_tables_from_pdf,
        guess_table_name_from_preview,
        preview_text_for_rows,
    )
    from services.budget_scoring import score_budget_table_likelihood

    raw = extract_tables_from_pdf(GOLDEN_PDF)
    candidates: list[dict] = []
    for idx, table in enumerate(raw):
        rows = table.get("rows") or []
        if len(rows) < 3:
            continue
        page = int(table.get("page") or 0)
        preview = preview_text_for_rows(rows)
        nome = (
            guess_table_name_from_preview(preview, idx)
            or str(table.get("section_name") or "")
        )
        score = score_budget_table_likelihood(rows)
        match = match_profile(rows, table_name=nome)
        candidates.append(
            {
                "id": f"t{idx}",
                "page": page,
                "nome": nome,
                "rows": rows,
                "score": score,
                "profile_id": match.profile_id,
                "table_kind": match.table_kind,
                "confidence": match.confidence,
            }
        )

    # Top por score (como o UI), filtrando sintético quando presente
    ranked = sorted(candidates, key=lambda c: float(c["score"]), reverse=True)
    top = ranked[:12]
    print("\nTop candidatos:")
    for c in top:
        print(
            f"  pág {c['page']:>3} score={c['score']:>6.1f} "
            f"profile={c['profile_id']:<22} kind={c['table_kind']:<12} "
            f"conf={c['confidence']:.2f} | {c['nome'][:50]}"
        )

    sintetico = [
        c
        for c in ranked
        if c["table_kind"] == "sintetico" or c["profile_id"] == "novacap_sintetico"
    ]
    # Preferir páginas 2–3 do golden (Orçamento Sintético + continuação)
    if sintetico:
        selected = [c for c in sintetico if c["page"] in {2, 3}] or sintetico[:2]
    else:
        selected = top[:6]

    print(f"\nSelecionadas para ABC ({len(selected)}):")
    all_items: list[dict] = []
    for c in selected:
        print(f"  pág {c['page']} profile={c['profile_id']} kind={c['table_kind']}")
        items = _items_from_table_rows(
            c["rows"],
            page=c["page"],
            table_id=c["id"],
            table_name=c["nome"],
            profile_id=c["profile_id"],
        )
        for item in items:
            item["_table_kind"] = c["table_kind"]
            item["_profile_id"] = c["profile_id"]
            item["_source_table_name"] = c["nome"]
        all_items.extend(items)

    filtered = _filter_for_analysis(_deduplicate_items(all_items), ["curva_abc"])
    classified = classify_abc_items(filtered)
    summary = build_abc_summary(classified)
    total = float(summary.get("total_value") or 0)

    print(f"\nItens ABC: {len(classified)}")
    print(f"Total ABC: {brl(total)}")
    print(f"Esperado:  {brl(GOLDEN_TOTAL)}")

    assert abs(total - GOLDEN_TOTAL) <= TOLERANCE, (
        f"Total ABC {brl(total)} ≠ golden {brl(GOLDEN_TOTAL)}"
    )
    assert len(classified) >= 40, f"Poucas folhas executivas: {len(classified)}"


def main() -> None:
    print("=== Perfis (amostras) ===")
    test_profile_matching_samples()
    print("OK amostras\n")

    print("=== Golden PDF NOVACAP ===")
    test_golden_pdf_sintetico()
    print("\nOK golden R$ 9.055.082,16")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFALHOU: {exc}")
        sys.exit(1)
    except Exception as exc:
        print(f"\nERRO: {exc}")
        raise
