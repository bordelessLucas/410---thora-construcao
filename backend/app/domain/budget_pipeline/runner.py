"""
Orquestrador do pipeline de 5 engines.

PDF → Layout → Table → Validator → Normalizer → Analytics
OpenAI NÃO participa da extração.

Auto-descobre páginas de planilha quando `pages` é None ou quando a seleção
inicial falha na validação (PDFs multi-layout).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.domain.budget_pipeline.engine1_layout import (
    analyze_pdf_layout,
    extract_document_total_from_elements,
)
from app.domain.budget_pipeline.engine2_table import reconstruct_tables
from app.domain.budget_pipeline.engine3_validator import validate_financial
from app.domain.budget_pipeline.engine4_normalizer import incomplete_to_dict, normalize_items
from app.domain.budget_pipeline.engine5_analytics import run_analytics
from app.domain.budget_pipeline.models import PipelineResult
from app.domain.budget_pipeline.page_discovery import discover_budget_pages
from app.domain.budget_pipeline.estimativo_meta import extract_estimativo_metadata
from app.infrastructure.pdf.word_layout import extract_document_total_geral

logger = logging.getLogger(__name__)


class PipelineRejectedError(Exception):
    """Extração rejeitada pelo validador financeiro."""

    def __init__(self, message: str, *, result: PipelineResult | None = None):
        super().__init__(message)
        self.result = result


def _run_once(
    path: Path,
    *,
    pages: list[int] | None,
    y_tolerance: float,
    run_abc: bool,
) -> PipelineResult:
    logs: list[dict[str, Any]] = []

    elements, meta1 = analyze_pdf_layout(path, pages=pages)
    logs.append(meta1)
    if not elements:
        raise PipelineRejectedError(
            "Nenhum texto nativo encontrado no PDF (OCR não suportado neste pipeline)."
        )

    # Total Geral: primeiro nas páginas lidas; senão no PDF inteiro (capa)
    document_total = extract_document_total_from_elements(elements)
    if document_total is None:
        document_total = extract_document_total_geral(path)

    economic_rows, incomplete_rows, meta2 = reconstruct_tables(
        elements, y_tolerance=y_tolerance
    )
    logs.append(meta2)
    if not economic_rows:
        raise PipelineRejectedError(
            "Nenhuma linha orçamentária reconstruída pelas coordenadas.",
            result=PipelineResult(
                incomplete=incomplete_to_dict(incomplete_rows),
                engine_logs=logs,
                document_total=document_total,
                pages_processed=meta1.get("pages") or [],
            ),
        )

    items_raw, validation, meta3 = validate_financial(
        economic_rows, document_total=document_total
    )
    logs.append(meta3)

    result = PipelineResult(
        incomplete=incomplete_to_dict(incomplete_rows),
        validation=validation,
        engine_logs=logs,
        document_total=validation.total_geral_documento or document_total,
        pages_processed=meta1.get("pages") or [],
    )

    if not validation.ok:
        result.items = items_raw
        return result

    normalized, hierarchy, meta4 = normalize_items(items_raw)
    logs.append(meta4)
    result.hierarchy = hierarchy

    if run_abc:
        classified, abc_summary, meta5 = run_analytics(normalized)
        logs.append(meta5)
        result.items = classified
        result.abc_summary = abc_summary
    else:
        result.items = normalized

    result.engine_logs = logs
    return result


def run_pipeline(
    pdf_path: Path | str,
    *,
    pages: list[int] | None = None,
    y_tolerance: float = 2.0,
    reject_on_validation_failure: bool = True,
    run_abc: bool = True,
    auto_discover_pages: bool = True,
) -> PipelineResult:
    """
    Executa engines 1–5.

    pages: índices 0-based. Se None e auto_discover_pages=True, descobre
    automaticamente as páginas de planilha. Se a seleção falhar na validação,
    tenta de novo com páginas descobertas.
    """
    path = Path(pdf_path)
    hint = list(pages) if pages is not None else None
    estimativo_meta = extract_estimativo_metadata(path)

    if pages is None and auto_discover_pages:
        pages = discover_budget_pages(path)
        logger.info("[pipeline] páginas auto-descobertas=%s", [p + 1 for p in pages])

    attempts: list[tuple[str, list[int] | None, float]] = [
        ("primary", pages, y_tolerance),
    ]

    # Retry: descoberta automática se a seleção explícita falhar
    if auto_discover_pages and hint is not None:
        discovered = discover_budget_pages(path, hint_pages=hint)
        if discovered and discovered != sorted(hint):
            attempts.append(("discovered", discovered, y_tolerance))
        global_disc = discover_budget_pages(path)
        known = {tuple(sorted(hint))}
        if discovered:
            known.add(tuple(discovered))
        if global_disc and tuple(global_disc) not in known:
            attempts.append(("global_discovery", global_disc, y_tolerance))

    # Retry com Y mais frouxo (PDFs com linhas “altas”)
    attempts.append(("y_loose", pages, max(y_tolerance, 3.5)))

    best: PipelineResult | None = None
    last_error: Exception | None = None

    def _attach_meta(result: PipelineResult) -> PipelineResult:
        if estimativo_meta:
            result.estimativo_meta = dict(estimativo_meta)
            result.document_kind = str(
                estimativo_meta.get("document_kind") or "estimativo_srp"
            )
        return result

    seen_keys: set[tuple] = set()
    for label, attempt_pages, yt in attempts:
        key = (tuple(attempt_pages) if attempt_pages is not None else None, yt)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        try:
            result = _run_once(
                path, pages=attempt_pages, y_tolerance=yt, run_abc=run_abc
            )
        except PipelineRejectedError as exc:
            last_error = exc
            if exc.result:
                _attach_meta(exc.result)
            if exc.result and (
                best is None
                or len(exc.result.items) > len(best.items)
                or (
                    exc.result.validation.ok
                    and not (best.validation.ok if best else False)
                )
            ):
                best = exc.result
            logger.warning("[pipeline] tentativa %s rejeitada cedo: %s", label, exc)
            continue

        result.engine_logs = list(result.engine_logs) + [
            {"engine": "runner", "attempt": label, "pages": attempt_pages, "y": yt}
        ]
        _attach_meta(result)

        if result.validation.ok:
            logger.info(
                "[pipeline] OK attempt=%s pages=%s items=%s abc_total=%s kind=%s",
                label,
                result.pages_processed,
                len(result.items),
                (result.abc_summary or {}).get("total_value"),
                result.document_kind,
            )
            return result

        logger.warning(
            "[pipeline] tentativa %s validação falhou: %s",
            label,
            result.validation.errors,
        )
        if best is None or (
            result.validation.soma_folhas > (best.validation.soma_folhas or 0)
            and len(result.validation.errors) <= len(best.validation.errors)
        ):
            best = result

    if best and best.validation.ok:
        return _attach_meta(best)

    if reject_on_validation_failure:
        msg = (
            "; ".join((best.validation.errors if best else []) or [])
            or (str(last_error) if last_error else "Validação financeira falhou")
        )
        logger.error("[pipeline] REJEITADO: %s", msg)
        raise PipelineRejectedError(msg, result=best)

    if best is None and last_error:
        raise last_error
    if best is None:
        raise PipelineRejectedError("Pipeline não produziu resultado")
    return _attach_meta(best)
