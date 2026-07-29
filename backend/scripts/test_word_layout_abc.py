"""
Golden: pipeline por coordenadas (word_layout) → ABC = R$ 9.055.082,16.

Uso:
  cd backend && uv run python scripts/test_word_layout_abc.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.abc_curve import build_abc_summary, classify_abc_items  # noqa: E402
from app.domain.financial_validation import (  # noqa: E402
    validate_extraction_finance,
    validate_total_geral,
)
from app.domain.money import parse_brl  # noqa: E402
from app.domain.profiles import match_profile  # noqa: E402
from app.domain.services.orcamento_extraction import (  # noqa: E402
    _deduplicate_items,
    _filter_for_analysis,
    _items_from_table_rows,
)
from app.infrastructure.pdf.word_layout import (  # noqa: E402
    extract_document_total_geral,
    extract_table_from_page_words,
    group_words_into_lines,
    normalize_row_cells,
)

GOLDEN_TOTAL = 9_055_082.16
GOLDEN_PDF = ROOT / "test_pdfs" / "planilha_orcamentaria_analise.pdf"
TOLERANCE = 1.0


def brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def test_normalize_cells() -> None:
    assert normalize_row_cells(["m² 7265,74", "8,92", "64.810,40", "0,72", "%"]) == [
        "m²",
        "7265,74",
        "8,92",
        "64.810,40",
        "0,72 %",
    ]
    assert normalize_row_cells(["194,72 1.697.742,26", "18,75 %"]) == [
        "194,72",
        "1.697.742,26",
        "18,75 %",
    ]
    print("  OK normalize_row_cells")


def test_group_by_y() -> None:
    words = [
        {"text": "1.1.1", "x0": 40, "x1": 50, "top": 100.0, "bottom": 110},
        {"text": "SINAPI", "x0": 100, "x1": 120, "top": 100.4, "bottom": 110},
        {"text": "desc", "x0": 150, "x1": 180, "top": 112.0, "bottom": 122},
    ]
    lines = group_words_into_lines(words, y_tolerance=2.0)
    assert len(lines) == 2
    assert lines[0]["text"] == "1.1.1 SINAPI"
    print("  OK group_words_into_lines")


def test_word_layout_golden_abc() -> None:
    import pdfplumber

    if not GOLDEN_PDF.exists():
        raise FileNotFoundError(GOLDEN_PDF)

    all_items: list[dict] = []
    with pdfplumber.open(GOLDEN_PDF) as pdf:
        # Orçamento Sintético nas páginas 2–3 (0-indexed: 1,2)
        for page_index in (1, 2):
            entry = extract_table_from_page_words(pdf.pages[page_index], page_index)
            assert entry is not None, f"word_layout falhou na página {page_index + 1}"
            rows = entry["rows"]
            match = match_profile(rows, table_name="Orçamento Sintético")
            print(
                f"  pág {page_index + 1}: rows={len(rows)} "
                f"profile={match.profile_id} conf={match.confidence:.2f} "
                f"method={entry.get('extraction_method')}"
            )
            items = _items_from_table_rows(
                rows,
                page=page_index + 1,
                table_id=entry["table_id"],
                table_name="Orçamento Sintético",
                profile_id=match.profile_id,
            )
            for item in items:
                item["_table_kind"] = match.table_kind
                item["_profile_id"] = match.profile_id
                item["_source_table_name"] = "Orçamento Sintético"
            all_items.extend(items)

    filtered = _filter_for_analysis(_deduplicate_items(all_items), ["curva_abc"])
    classified = classify_abc_items(filtered)
    summary = build_abc_summary(classified)
    total = float(summary.get("total_value") or 0)

    doc_total = extract_document_total_geral(GOLDEN_PDF)
    assert doc_total is not None
    assert abs(doc_total - GOLDEN_TOTAL) <= TOLERANCE, f"Total Geral PDF={doc_total}"

    finance = validate_extraction_finance(
        classified,
        expected_total=doc_total,
        abs_tolerance=TOLERANCE,
    )
    total_check = validate_total_geral(classified, doc_total, abs_tolerance=TOLERANCE)

    print(f"  Itens ABC: {summary['total_items']}")
    print(f"  Total ABC: {brl(total)}")
    print(f"  Total Geral PDF: {brl(doc_total)}")
    print(f"  Validação: ok={finance['ok']} alertas={finance.get('alertas')}")

    assert abs(total - GOLDEN_TOTAL) <= TOLERANCE, (
        f"Total ABC {brl(total)} ≠ golden {brl(GOLDEN_TOTAL)}"
    )
    assert summary["total_items"] >= 50
    assert total_check["ok"], total_check.get("alerta")
    assert finance["ok"], finance.get("alertas")


def test_parse_brl_chatgpt_rule() -> None:
    assert parse_brl("339.546,89") == 339546.89
    print("  OK parse_brl Etapa 9")


def main() -> None:
    print("=== Unidades ===")
    test_normalize_cells()
    test_group_by_y()
    test_parse_brl_chatgpt_rule()
    print("OK unidades\n")

    print("=== Golden word_layout → ABC ===")
    test_word_layout_golden_abc()
    print("\nOK word_layout R$ 9.055.082,16")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as exc:
        print(f"\nFALHOU: {exc}")
        sys.exit(1)
    except Exception:
        print("\nERRO:")
        raise
