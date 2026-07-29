"""
Golden + unitários do pipeline 5 engines.

Uso:
  cd backend && uv run python scripts/test_budget_pipeline_golden.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.domain.budget_pipeline import PipelineRejectedError, run_pipeline  # noqa: E402
from app.domain.budget_pipeline.engine1_layout import analyze_pdf_layout  # noqa: E402
from app.domain.budget_pipeline.engine2_table import reconstruct_tables  # noqa: E402
from app.domain.budget_pipeline.engine3_validator import validate_financial  # noqa: E402
from app.domain.budget_pipeline.engine4_normalizer import normalize_items  # noqa: E402
from app.domain.budget_pipeline.engine5_analytics import run_analytics  # noqa: E402
from app.domain.budget_pipeline.models import RawBudgetRow  # noqa: E402

GOLDEN_TOTAL = 9_055_082.16
GOLDEN_LEAVES = 51
GOLDEN_PDF = ROOT / "test_pdfs" / "planilha_orcamentaria_analise.pdf"
TOLERANCE = 0.01


def brl(value: float) -> str:
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def test_engine1_layout() -> None:
    assert GOLDEN_PDF.exists(), GOLDEN_PDF
    elements, meta = analyze_pdf_layout(GOLDEN_PDF, pages=[1, 2])
    assert meta["engine"] == "layout_analyzer"
    assert len(elements) > 100
    assert all(e.page in {2, 3} for e in elements)
    print(f"  OK engine1 layout elements={len(elements)}")


def test_engine2_table() -> None:
    elements, _ = analyze_pdf_layout(GOLDEN_PDF, pages=[1, 2])
    economic, incomplete, meta = reconstruct_tables(elements)
    assert meta["engine"] == "table_reconstruction"
    assert len(economic) >= GOLDEN_LEAVES
    print(
        f"  OK engine2 table economic={len(economic)} incomplete={len(incomplete)}"
    )


def test_engine3_reject_missing_child() -> None:
    """Filho faltando → soma ≠ Total Geral → rejeição."""
    rows = [
        RawBudgetRow(
            item_numero="1",
            descricao="Grupo",
            valor_total=100.0,
            kind="group",
            page=1,
        ),
        RawBudgetRow(
            item_numero="1.1",
            codigo="X",
            descricao="Folha parcial",
            unidade="un",
            quantidade=1,
            valor_unitario=50.0,
            valor_total=50.0,
            kind="item",
            page=1,
        ),
    ]
    items, report, meta = validate_financial(rows, document_total=100.0)
    assert meta["engine"] == "financial_validator"
    assert report.ok is False
    assert abs((report.diferenca_total or 0) - 50.0) <= TOLERANCE
    print("  OK engine3 rejeita filho faltando")


def test_engine3_accept_exact() -> None:
    rows = [
        RawBudgetRow(
            item_numero="1.1",
            codigo="A",
            descricao="Item",
            unidade="un",
            quantidade=1,
            valor_unitario=10.0,
            valor_total=10.0,
            kind="item",
            page=1,
        ),
        RawBudgetRow(
            item_numero="1.2",
            codigo="B",
            descricao="Item 2",
            unidade="un",
            quantidade=1,
            valor_unitario=5.01,
            valor_total=5.01,
            kind="item",
            page=1,
        ),
    ]
    _, report, _ = validate_financial(rows, document_total=15.01)
    assert report.ok is True
    print("  OK engine3 aceita total exato")


def test_engines_4_5_normalize_abc() -> None:
    rows = [
        RawBudgetRow(
            item_numero="1.1",
            codigo="A",
            descricao="Maior",
            unidade="un",
            quantidade=1,
            valor_unitario=80.0,
            valor_total=80.0,
            kind="item",
            page=1,
        ),
        RawBudgetRow(
            item_numero="1.2",
            codigo="B",
            descricao="Menor",
            unidade="un",
            quantidade=1,
            valor_unitario=20.0,
            valor_total=20.0,
            kind="item",
            page=1,
        ),
    ]
    items, report, _ = validate_financial(rows, document_total=100.0)
    assert report.ok
    normalized, hierarchy, meta4 = normalize_items(items)
    assert meta4["engine"] == "normalizer"
    assert hierarchy
    classified, summary, meta5 = run_analytics(normalized)
    assert meta5["engine"] == "analytics"
    assert summary["total_value"] == 100.0
    assert any(i.get("classification") == "A" for i in classified)
    print("  OK engine4+5 normalizer/ABC")


def test_pipeline_golden() -> None:
    assert GOLDEN_PDF.exists(), GOLDEN_PDF
    result = run_pipeline(
        GOLDEN_PDF,
        pages=[1, 2],
        reject_on_validation_failure=True,
        run_abc=True,
    )
    assert result.validation.ok is True
    total = float((result.abc_summary or {}).get("total_value") or 0)
    leaves = int((result.abc_summary or {}).get("total_items") or 0)
    assert abs(total - GOLDEN_TOTAL) <= TOLERANCE, (
        f"Total {brl(total)} ≠ golden {brl(GOLDEN_TOTAL)}"
    )
    assert leaves == GOLDEN_LEAVES, f"folhas={leaves} esperado={GOLDEN_LEAVES}"
    engines = [log.get("engine") for log in result.engine_logs if log.get("engine") != "runner"]
    assert engines == [
        "layout_analyzer",
        "table_reconstruction",
        "financial_validator",
        "normalizer",
        "analytics",
    ], engines
    print(
        f"  OK golden pipeline total={brl(total)} folhas={leaves} engines={engines}"
    )


def test_pipeline_reject_raises() -> None:
    """Garante que validação hard dispara PipelineRejectedError."""
    raised = False
    try:
        # Capa sozinha, sem auto-descoberta → sem linhas econômicas
        run_pipeline(
            GOLDEN_PDF,
            pages=[0],
            reject_on_validation_failure=True,
            run_abc=False,
            auto_discover_pages=False,
        )
    except PipelineRejectedError as exc:
        raised = True
        assert exc.result is not None or str(exc)
        print(f"  OK pipeline reject: {exc}")
    assert raised, "esperava PipelineRejectedError sem auto-descoberta"


def test_pipeline_exemplo_us_format() -> None:
    """Planilha simples com decimal US (35.50) e códigos 001…"""
    exemplo = ROOT / "test_pdfs" / "exemplo.pdf"
    if not exemplo.exists():
        print("  SKIP exemplo.pdf ausente")
        return
    result = run_pipeline(
        exemplo,
        pages=[0],
        auto_discover_pages=False,
        reject_on_validation_failure=True,
        run_abc=True,
    )
    assert result.validation.ok
    assert abs(float((result.abc_summary or {}).get("total_value") or 0) - 44800.0) <= TOLERANCE
    assert int((result.abc_summary or {}).get("total_items") or 0) == 5
    print("  OK exemplo.pdf US-format total=R$ 44.800,00 folhas=5")


def test_pipeline_auto_discover_golden() -> None:
    from app.domain.budget_pipeline.page_discovery import discover_budget_pages

    pages = discover_budget_pages(GOLDEN_PDF)
    assert pages[:2] == [1, 2] or pages == [1, 2], pages
    result = run_pipeline(GOLDEN_PDF, pages=None, reject_on_validation_failure=True, run_abc=True)
    assert abs(float((result.abc_summary or {}).get("total_value") or 0) - GOLDEN_TOTAL) <= TOLERANCE
    assert int((result.abc_summary or {}).get("total_items") or 0) == GOLDEN_LEAVES
    print(f"  OK auto-discover pages={[p+1 for p in pages]} total={brl(GOLDEN_TOTAL)}")


def test_no_openai_in_extraction_path() -> None:
    """Sanidade: runner não importa OpenAI nem insights."""
    import app.domain.budget_pipeline.runner as runner_mod

    src = Path(runner_mod.__file__).read_text(encoding="utf-8")
    assert "import openai" not in src
    assert "from openai" not in src
    assert "insights_openai" not in src
    print("  OK runner sem import OpenAI")


def main() -> int:
    print("=== test_budget_pipeline_golden ===")
    test_engine1_layout()
    test_engine2_table()
    test_engine3_reject_missing_child()
    test_engine3_accept_exact()
    test_engines_4_5_normalize_abc()
    test_pipeline_golden()
    test_pipeline_reject_raises()
    test_pipeline_exemplo_us_format()
    test_pipeline_auto_discover_golden()
    test_no_openai_in_extraction_path()
    print("TODOS OS TESTES PASSARAM")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
