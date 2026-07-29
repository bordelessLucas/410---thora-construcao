"""
Engine 1 — PDF Layout Analyzer.

Lê coordenadas nativas (pdfplumber / pdfminer). Sem OCR quando há texto.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pdfplumber

from app.domain.budget_pipeline.models import TextElement

logger = logging.getLogger(__name__)


def analyze_pdf_layout(
    pdf_path: Path | str,
    *,
    pages: list[int] | None = None,
) -> tuple[list[TextElement], dict[str, Any]]:
    """
    Extrai todos os elementos de texto com coordenadas.

    pages: índices 0-based; None = todas.
    Retorna (elementos, log_meta).
    """
    path = Path(pdf_path)
    elements: list[TextElement] = []
    page_counts: dict[int, int] = {}

    with pdfplumber.open(path) as pdf:
        indices = pages if pages is not None else list(range(len(pdf.pages)))
        for page_index in indices:
            if page_index < 0 or page_index >= len(pdf.pages):
                continue
            page = pdf.pages[page_index]
            page_num = page_index + 1
            try:
                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=2,
                    keep_blank_chars=False,
                    use_text_flow=False,
                    extra_attrs=["fontname", "size"],
                )
            except TypeError:
                # pdfplumber antigo sem extra_attrs
                words = page.extract_words(
                    x_tolerance=2,
                    y_tolerance=2,
                    keep_blank_chars=False,
                    use_text_flow=False,
                )
            except Exception as exc:
                logger.warning("[engine1] extract_words pág %s: %s", page_num, exc)
                continue

            count = 0
            for w in words or []:
                text = str(w.get("text") or "")
                # Preservar texto exatamente como encontrado (só ignora vazio puro)
                if text == "":
                    continue
                elements.append(
                    TextElement(
                        text=text,
                        x0=float(w["x0"]),
                        y0=float(w["top"]),
                        x1=float(w["x1"]),
                        y1=float(w.get("bottom") or w["top"]),
                        page=page_num,
                        fontname=str(w.get("fontname") or ""),
                        size=float(w.get("size") or 0.0),
                    )
                )
                count += 1
            page_counts[page_num] = count
            logger.info(
                "[engine1] pág %s: %s elementos (%sx%s)",
                page_num,
                count,
                round(float(page.width), 1),
                round(float(page.height), 1),
            )

    meta = {
        "engine": "layout_analyzer",
        "pages": sorted(page_counts.keys()),
        "elements_per_page": page_counts,
        "total_elements": len(elements),
    }
    logger.info("[engine1] total elementos=%s páginas=%s", len(elements), meta["pages"])
    return elements, meta


def extract_document_total_from_elements(elements: list[TextElement]) -> float | None:
    """Procura Total Geral / Valor da Contratação / Preço com BDI no texto nativo."""
    import re

    from app.domain.money import parse_brl

    by_page: dict[int, list[TextElement]] = {}
    for el in elements:
        by_page.setdefault(el.page, []).append(el)

    patterns = [
        re.compile(
            r"valor\s+da\s+contrata[cç][aã]o\s*:?\s*R\$\s*([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:or[cç]amento\s+)?pre[cç]o\s+total\s+com\s+bdi"
            r"(?:\s+\d{1,3},\d{2}\s*%)?\s*R\$\s*([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:total\s+geral|valor\s+da\s+obra|pre[cç]o\s+global|"
            r"valor\s+global|total\s+do\s+or[cç]amento|"
            r"valor\s+final\s+do\s+or[cç]amento)\s*:?\s*R\$\s*([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"valor\s+final\s+do\s+or[cç]amento\s+(?:modelo\s+)?([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"TOTAL:\s*R\$\s*([\d.]+,\d{2})\s+R\$\s*([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(r"TOTAL:\s*R\$\s*([\d.]+,\d{2})", re.IGNORECASE),
    ]

    best: float | None = None
    for page in sorted(by_page.keys())[:8]:
        text = " ".join(e.text for e in sorted(by_page[page], key=lambda e: (e.y0, e.x0)))
        for pattern in patterns:
            match = pattern.search(text)
            if not match:
                continue
            groups = [g for g in match.groups() if g]
            values = [parse_brl(g) for g in groups]
            # Ignora falsos positivos tipo "100,00%" capturados como total
            values = [v for v in values if v >= 1_000]
            if not values:
                continue
            value = max(values)
            if best is None or value > best:
                best = value
    return best
