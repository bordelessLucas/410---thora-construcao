from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)

_PDFPLUMBER_TEXT_TABLE_SETTINGS = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "edge_min_length": 3,
}
_PDFPLUMBER_LINES_TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 5,
    "join_tolerance": 5,
    "edge_min_length": 3,
}

PREVIEW_ROWS_LIMIT = 8


def preview_text_for_rows(rows: list[list[Any]], max_chars: int = 280) -> str:
    snippets: list[str] = []
    for row in rows[:4]:
        line = " | ".join(str(c)[:60] if c is not None else "" for c in row)
        if line.strip():
            snippets.append(line.strip())
    text = " · ".join(snippets)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def preview_rows_for_api(rows: list[list[Any]], limit: int = PREVIEW_ROWS_LIMIT) -> list[list[str]]:
    preview: list[list[str]] = []
    for row in rows[:limit]:
        preview.append([str(c).strip() if c is not None else "" for c in row])
    return preview


def count_nonempty_rows(rows: list[list[Any]]) -> int:
    return sum(1 for row in rows if any(str(cell).strip() for cell in row))


def normalize_table_rows(table: list[list[Any]]) -> list[list[Any]]:
    processed: list[list[Any]] = []
    for row in table:
        processed_row: list[Any] = []
        for cell in row:
            if cell is None:
                processed_row.append("")
            elif isinstance(cell, str):
                processed_row.append(cell.strip().replace("\n", " "))
            else:
                processed_row.append(str(cell))
        processed.append(processed_row)
    return processed


def _table_header_fingerprint(rows: list[list[Any]]) -> str:
    parts: list[str] = []
    for row in rows[:4]:
        parts.append(" ".join(str(c).lower().strip() for c in row if str(c).strip()))
    return "|".join(parts)[:240]


def _best_table_signal(tables: list[list[list[Any]]]) -> tuple[int, int]:
    from services.budget_scoring import score_budget_table_likelihood

    if not tables:
        return 0, 0
    best_nonempty = 0
    best_score = 0
    for table in tables:
        if not table:
            continue
        rows = normalize_table_rows(table)
        best_nonempty = max(best_nonempty, count_nonempty_rows(rows))
        best_score = max(best_score, score_budget_table_likelihood(rows))
    return best_nonempty, best_score


def _bbox_overlap_ratio(
    a: tuple[float, ...] | list[float] | None,
    b: tuple[float, ...] | list[float] | None,
) -> float:
    if not a or not b or len(a) < 4 or len(b) < 4:
        return 0.0
    ax0, ay0, ax1, ay1 = float(a[0]), float(a[1]), float(a[2]), float(a[3])
    bx0, by0, bx1, by1 = float(b[0]), float(b[1]), float(b[2]), float(b[3])
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax1, bx1), min(ay1, by1)
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(1.0, (ax1 - ax0) * (ay1 - ay0))
    area_b = max(1.0, (bx1 - bx0) * (by1 - by0))
    return inter / min(area_a, area_b)


def _is_same_table_region(
    rows_a: list[list[Any]],
    bbox_a: Any,
    rows_b: list[list[Any]],
    bbox_b: Any,
) -> bool:
    fp_a = _table_header_fingerprint(rows_a)
    fp_b = _table_header_fingerprint(rows_b)
    if fp_a and fp_a == fp_b:
        overlap = _bbox_overlap_ratio(bbox_a, bbox_b)
        if overlap >= 0.35:
            return True
        if not bbox_a or not bbox_b:
            return True
    if bbox_a and bbox_b and _bbox_overlap_ratio(bbox_a, bbox_b) >= 0.82:
        return True
    return False


def _collect_tables_from_finder(
    found_tables: list[Any],
    collected: list[dict[str, Any]],
    page_index: int,
) -> None:
    from app.infrastructure.pdf.table_split import split_pdfplumber_table

    for table in found_tables:
        try:
            raw = table.extract()
        except Exception as exc:
            logger.debug("extract table falhou: %s", exc)
            continue
        if not raw:
            continue

        table_seq = len(collected)
        segments = split_pdfplumber_table(table, page_index, table_seq=table_seq)
        candidates = segments if segments else []

        if not candidates:
            rows = normalize_table_rows(raw)
            if count_nonempty_rows(rows) < 2:
                continue
            bbox = getattr(table, "bbox", None)
            candidates = [
                {
                    "rows": rows,
                    "bbox": tuple(bbox) if bbox else None,
                    "table_id": f"page_{page_index}_table_{table_seq}",
                }
            ]

        for entry in candidates:
            entry_rows = entry.get("rows") or []
            entry_bbox = entry.get("bbox")
            if any(
                _is_same_table_region(
                    entry_rows,
                    entry_bbox,
                    existing.get("rows") or [],
                    existing.get("bbox"),
                )
                for existing in collected
            ):
                continue
            if not entry.get("table_id"):
                entry["table_id"] = f"page_{page_index}_table_{len(collected)}"
            collected.append(entry)


def _maybe_add_word_layout_table(
    page: Any,
    page_index: int,
    collected: list[dict[str, Any]],
    *,
    force: bool = False,
) -> None:
    """
    Pipeline por coordenadas (extract_words → Y → colunas X).

    Entra quando find_tables está fraco, ou quando o layout por palavras
    reconstrói melhor uma planilha com numeração hierárquica (sintético/edital).
    """
    from app.infrastructure.pdf.word_layout import extract_table_from_page_words
    from services.budget_scoring import score_budget_table_likelihood

    try:
        entry = extract_table_from_page_words(page, page_index)
    except Exception as exc:
        logger.debug("word_layout falhou pág %s: %s", page_index + 1, exc)
        return
    if not entry:
        return

    rows = entry.get("rows") or []
    wl_score = score_budget_table_likelihood(rows)
    wl_nonempty = count_nonempty_rows(rows)
    best_nonempty, best_score = _best_table_signal([t.get("rows") or [] for t in collected])

    # Sinal de planilha orçamentária hierárquica (não composição unitária)
    hier_items = 0
    for row in rows[:40]:
        if not row:
            continue
        first = str(row[0] or "").strip()
        if re.match(r"^\d+\.\d+", first):
            hier_items += 1
    looks_like_budget_sheet = hier_items >= 4

    # word_layout é para planilhas com numeração hierárquica (sintético/edital)
    if not looks_like_budget_sheet:
        return

    better = wl_score > best_score + 15
    weak_page = best_nonempty < 12 or best_score < 25
    if not force and not better and not weak_page:
        return

    # Evita duplicar a mesma região; substitui se word_layout for melhor
    for i, existing in enumerate(collected):
        if _is_same_table_region(
            rows,
            entry.get("bbox"),
            existing.get("rows") or [],
            existing.get("bbox"),
        ):
            existing_score = score_budget_table_likelihood(existing.get("rows") or [])
            if wl_score > existing_score:
                collected[i] = entry
                logger.info(
                    "word_layout substituiu find_tables pág %s: %s→%s",
                    page_index + 1,
                    existing_score,
                    wl_score,
                )
            return

    collected.append(entry)
    logger.info(
        "word_layout pág %s: score=%s linhas=%s hier=%s (find_tables best=%s/%s)",
        page_index + 1,
        wl_score,
        wl_nonempty,
        hier_items,
        best_score,
        best_nonempty,
    )


def extract_page_tables_with_bbox(page: Any, page_index: int) -> list[dict[str, Any]]:
    """Extrai tabelas com bbox via pdfplumber find_tables + fallback por coordenadas."""
    collected: list[dict[str, Any]] = []

    default_found = page.find_tables() or []
    _collect_tables_from_finder(default_found, collected, page_index)

    try:
        lines_found = page.find_tables(table_settings=_PDFPLUMBER_LINES_TABLE_SETTINGS) or []
        _collect_tables_from_finder(lines_found, collected, page_index)
    except Exception as exc:
        logger.debug("find_tables lines falhou: %s", exc)

    default_rows_only = [t["rows"] for t in collected]
    best_nonempty, best_score = _best_table_signal(default_rows_only)
    needs_fallback = best_nonempty < 12 or best_score < 25

    if needs_fallback:
        try:
            text_found = page.find_tables(table_settings=_PDFPLUMBER_TEXT_TABLE_SETTINGS) or []
            _collect_tables_from_finder(text_found, collected, page_index)
        except Exception as exc:
            logger.debug("find_tables text falhou: %s", exc)

    # Etapas 1–8: reconstrução espacial por palavras (genérico, sem OCR)
    _maybe_add_word_layout_table(
        page,
        page_index,
        collected,
        force=needs_fallback or not collected,
    )

    if not collected:
        text = page.extract_text()
        if text:
            lines = [line.strip() for line in text.split("\n") if line.strip()]
            if lines:
                collected.append(
                    {
                        "rows": [[line] for line in lines],
                        "bbox": None,
                        "table_id": f"page_{page_index}_table_0",
                    }
                )

    return collected


def extract_tables_from_pdf(
    file_path: Path,
    max_pages: int | None = None,
    on_page: Any | None = None,
) -> list[dict[str, Any]]:
    tables: list[dict[str, Any]] = []
    with pdfplumber.open(file_path) as pdf:
        total_pages = len(pdf.pages)
        scan_limit = min(total_pages, max_pages) if max_pages else total_pages
        logger.info(
            "Processando PDF: %s página(s)%s",
            total_pages,
            f" (limite {scan_limit})" if max_pages and scan_limit < total_pages else "",
        )

        for page_num, page in enumerate(pdf.pages):
            if max_pages is not None and page_num >= max_pages:
                break
            page_tables = extract_page_tables_with_bbox(page, page_num)
            for entry in page_tables:
                rows = entry.get("rows") or []
                tables.append(
                    {
                        "page": page_num + 1,
                        "table_id": entry.get("table_id") or f"page_{page_num}_table_0",
                        "rows": rows,
                        "bbox": entry.get("bbox"),
                        "section_name": entry.get("section_name"),
                        "original_rows": len(rows),
                        "columns": len(rows[0]) if rows else 0,
                    }
                )
            if on_page is not None:
                try:
                    on_page(page_num + 1, scan_limit, len(tables))
                except Exception as exc:
                    logger.debug("on_page callback: %s", exc)
    return tables


def count_pdf_pages(file_path: Path, max_pages: int | None = None) -> int:
    """Retorna quantas páginas serão analisadas (respeitando limite)."""
    with pdfplumber.open(file_path) as pdf:
        total = len(pdf.pages)
    if max_pages is None:
        return total
    return min(total, max_pages)


def extract_tables_from_single_page(
    file_path: Path,
    page_index: int,
) -> list[dict[str, Any]]:
    """
    Extrai tabelas de UMA página abrindo/fechando o PDF.
    Mais lento, mas reduz pico de RAM no Render Free.
    """
    tables: list[dict[str, Any]] = []
    with pdfplumber.open(file_path) as pdf:
        if page_index < 0 or page_index >= len(pdf.pages):
            return []
        page = pdf.pages[page_index]
        page_tables = extract_page_tables_with_bbox(page, page_index)
        for entry in page_tables:
            rows = entry.get("rows") or []
            tables.append(
                {
                    "page": page_index + 1,
                    "table_id": entry.get("table_id") or f"page_{page_index}_table_0",
                    "rows": rows,
                    "bbox": entry.get("bbox"),
                    "section_name": entry.get("section_name"),
                    "original_rows": len(rows),
                    "columns": len(rows[0]) if rows else 0,
                }
            )
    return tables


def guess_table_name_from_preview(preview_text: str, fallback_index: int) -> str:
    text = preview_text.lower()
    if "orçamento sintético" in text or "orcamento sintetico" in text:
        return "Orçamento Sintético"
    if "quantitativo" in text or "planilha" in text:
        return "Planilha de Quantitativos"
    if "composi" in text:
        return "Composições"
    if "cronograma" in text:
        return "Cronograma de Desembolso"
    return f"Tabela {fallback_index}"
