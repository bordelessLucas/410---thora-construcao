"""
Heurísticas compartilhadas para detectar páginas/tabelas de orçamento em PDFs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import BytesIO
from typing import Any, List, Tuple

import pdfplumber

_BUDGET_PAGE_KEYWORDS = (
    "sinapi",
    "sicro",
    "orse",
    "siurb",
    "agetop",
    "sco ",
    "composição",
    "composicao",
    "insumo",
    "planilha analítica",
    "planilha analitica",
    "planilha orçamentária",
    "planilha orcamentaria",
    "planilha de custos",
    "memorial descritivo",
    "quadro de preços",
    "quadro de quantidades",
    "valor unit",
    "valor unitário",
    "valor unitario",
    "preço unit",
    "preco unit",
    "preço total",
    "preco total",
    "valor global",
    "custo unit",
    "quant.",
    "quantidade",
    "qtde",
    "qtd ",
    "b.d.i",
    "bdi",
    " und ",
    "unidade",
    "m²",
    "m3",
    "r$",
    "reais",
    "serviço",
    "servico",
)

_STRONG_BUDGET_KEYWORDS = (
    "sinapi",
    "sicro",
    "planilha analítica",
    "planilha analitica",
    "planilha orçamentária",
    "planilha orcamentaria",
    "preço unit",
    "preco unit",
    "valor unit",
    "qtde",
    "quant.",
)

_EDITAL_NOISE_KEYWORDS = (
    "licitação",
    "licitacao",
    "edital",
    "decreto",
    "art.",
    "parágrafo",
    "microempresa",
    "certame",
    "fornecedor",
    "proposta",
    "habilitação",
    "habilitacao",
    "pregão",
    "pregao",
    "impugnação",
    "impugnacao",
    "recurso administrativo",
)

_BUDGET_HEADER_HINTS: dict[str, list[str]] = {
    "descricao": ["descrição", "descricao", "serviço", "servico", "do serviço", "do servico"],
    "quantidade": ["qtde", "quant", "quantidade", "qtd"],
    "valor": [
        "preço unit",
        "preco unit",
        "valor unit",
        "p. unit",
        "p.unit",
        "unitário",
        "unitario",
        "preço total",
        "preco total",
    ],
    "codigo": ["código", "codigo", "code"],
    "bdi": ["bdi", "% bdi"],
}

# Aggressive blacklist to exclude juridical/edital pages
BLACKLIST_WORDS = [
    "multa",
    "penalidade",
    "adjudicatário",
    "adjudicatario",
    "subcontratação",
    "subcontratacao",
    "licitante",
    "habilitação",
    "habilitacao",
    "recurso",
    "impugnação",
    "impugnacao",
    "cláusula",
    "clausula",
]

# Required engineering whitelist terms (must appear for a page to be considered)
WHITELIST_WORDS = [
    "sinapi",
    "sicro",
    "bdi",
    "encargos",
    "unid",
    "quant",
    "composições",
    "composicoes",
    "insumo",
    "m³",
    "m2",
    "m²",
]

_SERVICE_CODE_PATTERN = re.compile(
    r"\b(CPU\d+|[A-Z]{2,}\d{3,}|\d{5,}[A-Z]?)\b",
    re.IGNORECASE,
)

_NUMERIC_CELL_PATTERN = re.compile(r"\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}")
_CURRENCY_PATTERN = re.compile(
    r"R\$\s*\d{1,3}(?:\.\d{3})*(?:,\d{2})?|\d{1,3}(?:\.\d{3})*,\d{2}\s*(?:reais)?",
    re.IGNORECASE,
)
_QUANTITY_WORD_PATTERN = re.compile(
    r"\b(qtde|quantidade|qtd|quant\.?|q\.\s*total)\b",
    re.IGNORECASE,
)
_ITEM_NUMBER_PATTERN = re.compile(r"\b\d{1,2}(?:\.\d{1,4}){1,3}\b")
_UNIT_PATTERN = re.compile(r"\b(un|und|m²|m2|m³|m3|kg|t|hh|h|l|km)\b", re.IGNORECASE)


def _coerce_number(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    text = text.replace("R$", "").replace("%", "").strip()
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return 0.0


def score_budget_table_likelihood(rows: List[List[Any]]) -> int:
    """Pontua se a matriz parece planilha de orçamento (não texto do edital)."""
    if not rows:
        return 0
    # Require clear budget table headers (Código, Quantidade, Valor Unitário)
    header_rows = rows[:3]
    header_text = " ".join(" ".join(str(c).lower() for c in row if c) for row in header_rows)

    # If header suggests edital clauses like prazo/multa/cláusula, reject immediately
    edital_forbidden = ("prazo", "multa", "multas", "cláusula", "clausula", "clausulas", "penalidade")
    if any(k in header_text for k in edital_forbidden):
        return 0

    # Check for mandatory header tokens
    has_codigo = any(any(k in str(c).lower() for k in _BUDGET_HEADER_HINTS["codigo"]) for row in header_rows for c in row if c)
    has_qtd = any(any(k in str(c).lower() for k in _BUDGET_HEADER_HINTS["quantidade"]) for row in header_rows for c in row if c)
    has_val = any(any(k in str(c).lower() for k in _BUDGET_HEADER_HINTS["valor"]) for row in header_rows for c in row if c)

    # Continuação de Orçamento Sintético (sem cabeçalho na página):
    # linhas X.Y.Z + código + valores + peso%
    xyz_rows = 0
    peso_rows = 0
    for row in rows[:25]:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if not cells:
            continue
        if re.match(r"^\d+\.\d+\.\d+", cells[0]):
            xyz_rows += 1
        if any("%" in c and len(c) < 16 for c in cells[-3:]):
            peso_rows += 1
    is_sintetico_continuation = xyz_rows >= 5 and peso_rows >= 5

    # Enforce presence of the three classical engineering columns
    # (exceto continuação de sintético, que herda o cabeçalho da página anterior)
    if not (has_codigo and has_qtd and has_val) and not is_sintetico_continuation:
        return 0

    score = 0
    if is_sintetico_continuation:
        score += 140  # Prioriza frente a composições na seleção automática
    sample_parts: list[str] = []
    for row in rows[:15]:
        sample_parts.append(" ".join(str(c).lower() for c in row if c))
    sample_text = " ".join(sample_parts)

    edital_hits = sum(1 for kw in _EDITAL_NOISE_KEYWORDS if kw in sample_text)
    if edital_hits >= 4:
        score -= 40
    elif edital_hits >= 2:
        score -= 15

    for row in rows[:18]:
        row_text = " ".join(str(c).lower() for c in row if c)
        has_desc = any(k in row_text for k in _BUDGET_HEADER_HINTS["descricao"])
        has_qtd = any(k in row_text for k in _BUDGET_HEADER_HINTS["quantidade"])
        has_val = any(k in row_text for k in _BUDGET_HEADER_HINTS["valor"])
        has_cod = any(k in row_text for k in _BUDGET_HEADER_HINTS["codigo"])
        has_bdi = any(k in row_text for k in _BUDGET_HEADER_HINTS["bdi"])
        if has_desc and (has_qtd or has_val):
            score += 28
        if has_cod and (has_qtd or has_val):
            score += 22
        if has_bdi and has_cod:
            score += 12

    code_rows = 0
    for row in rows[1 : min(45, len(rows))]:
        line = " ".join(str(c) for c in row if c)
        if _SERVICE_CODE_PATTERN.search(line):
            code_rows += 1
    score += min(code_rows * 4, 36)

    numeric_rows = 0
    for row in rows[1 : min(35, len(rows))]:
        nums = sum(1 for c in row if _coerce_number(c) > 0)
        if nums >= 2:
            numeric_rows += 1
    score += min(numeric_rows * 2, 20)

    if "orçamento" in sample_text or "orcamento" in sample_text:
        score += 8
    if "sintético" in sample_text or "sintetico" in sample_text:
        score += 40
    if "composição" in sample_text or "composicao" in sample_text:
        # Composição detalhada não deve ganhar da planilha sintética
        score += 6

    return score


def _keyword_score(text: str) -> tuple[int, int]:
    lowered = text.lower()
    general = sum(1 for kw in _BUDGET_PAGE_KEYWORDS if kw in lowered)
    strong = sum(1 for kw in _STRONG_BUDGET_KEYWORDS if kw in lowered)
    return general, strong


def _edital_noise_score(text: str) -> int:
    lowered = text.lower()
    return sum(1 for kw in _EDITAL_NOISE_KEYWORDS if kw in lowered)


def _text_has_budget_numeric_pattern(text: str) -> bool:
    currencies = len(_CURRENCY_PATTERN.findall(text)) + len(_NUMERIC_CELL_PATTERN.findall(text))
    return (
        currencies >= 2
        or len(_SERVICE_CODE_PATTERN.findall(text)) >= 2
        or (
            currencies >= 1
            and len(_QUANTITY_WORD_PATTERN.findall(text)) >= 1
            and len(_UNIT_PATTERN.findall(text)) >= 1
        )
    )


def score_text_budget_likelihood(text: str) -> int:
    """
    Pontua páginas com orçamento em texto corrido (edital, memorial, quadros descritivos).
    Independente de tabelas pdfplumber.
    """
    if not text.strip():
        return 0

    lowered = text.lower()
    score = 0
    currencies = len(_CURRENCY_PATTERN.findall(text)) + len(_NUMERIC_CELL_PATTERN.findall(text))
    quantities = len(_QUANTITY_WORD_PATTERN.findall(text))
    codes = len(_SERVICE_CODE_PATTERN.findall(text))
    item_nums = len(_ITEM_NUMBER_PATTERN.findall(text))
    units = len(_UNIT_PATTERN.findall(text))

    score += min(currencies * 4, 36)
    score += min(quantities * 5, 25)
    score += min(codes * 4, 28)
    score += min(item_nums * 2, 16)
    score += min(units * 2, 12)

    kw_general, kw_strong = _keyword_score(text)
    score += kw_strong * 4 + min(kw_general, 6)

    if "valor unit" in lowered or "preço unit" in lowered or "preco unit" in lowered:
        score += 12
    if "orçamento" in lowered or "orcamento" in lowered:
        score += 8
    if "composição" in lowered or "composicao" in lowered:
        score += 6

    edital_noise = _edital_noise_score(text)
    if edital_noise >= 6 and currencies < 2 and quantities == 0:
        score -= 30
    elif edital_noise >= 4 and currencies < 1:
        score -= 12

    return score


@dataclass(frozen=True)
class BudgetPageCandidate:
    page_number: int
    image_detail: str  # "high" | "low" | "text"
    table_score: int
    keyword_score: int
    text_score: int = 0


def detect_budget_pages(
    pdf_content: bytes,
    *,
    max_pages: int = 60,
    min_table_score: int = 10,
    min_text_score: int = 4,
    min_strong_keywords: int = 1,
) -> List[BudgetPageCandidate]:
    """
    Retorna páginas candidatas (tabelas E texto corrido com valores/quantidades).
    """
    candidates: list[tuple[int, BudgetPageCandidate]] = []
    seen_pages: set[int] = set()

    with pdfplumber.open(BytesIO(pdf_content)) as pdf:
        total = min(len(pdf.pages), max_pages)
        for idx in range(total):
            page_num = idx + 1
            page = pdf.pages[idx]
            text = page.extract_text() or ""
            if not text.strip():
                continue

            kw_general, kw_strong = _keyword_score(text)
            text_score = score_text_budget_likelihood(text)
            has_numeric = _text_has_budget_numeric_pattern(text)
            quantities = len(_QUANTITY_WORD_PATTERN.findall(text))

            tables = page.extract_tables() or []
            table_scores = [
                score_budget_table_likelihood(table)
                for table in tables
                if table and any(any(str(c).strip() for c in row) for row in table)
            ]
            best_table = max(table_scores) if table_scores else 0

            # Blacklist check: if page contains multiple juridical/editais keywords -> skip
            lowered = text.lower()
            blacklist_hits = sum(1 for kw in BLACKLIST_WORDS if kw in lowered)
            if blacklist_hits >= 2:
                # penalize heavily and skip page
                continue

            # Whitelist requirement: page must contain at least one engineering hint
            whitelist_hits = sum(1 for kw in WHITELIST_WORDS if kw in lowered)

            # Numeric/codes heuristic: count long numeric tokens (codes) and currency occurrences
            numeric_codes = len(re.findall(r"\b\d{4,6}\b", text))
            currency_count = len(_CURRENCY_PATTERN.findall(text)) + len(_NUMERIC_CELL_PATTERN.findall(text))

            # Text density: average words per line (high -> likely prose/edital)
            lines = [ln for ln in text.splitlines() if ln.strip()]
            words = text.split()
            avg_words_per_line = (len(words) / len(lines)) if lines else 0
            if avg_words_per_line > 14 and currency_count < 2 and numeric_codes < 2:
                # very dense prose with few numbers -> likely edital text
                continue

            has_digits = bool(re.search(r"\d", text))
            is_table_page = best_table >= min_table_score
            # require whitelist presence unless a very strong table signal exists
            if whitelist_hits == 0 and not (best_table >= 24 or numeric_codes >= 3):
                # not engineering jargon and not a very strong table -> skip
                continue

            is_text_budget_page = text_score >= min_text_score and (
                has_numeric or (has_digits and len(text) > 120)
            )
            is_mixed_signal = (
                text_score >= 4
                and has_digits
                and (kw_general >= 1 or has_numeric)
            )
            is_loose_edital_page = (
                has_digits
                and len(text) > 80
                and (has_numeric or "r$" in text.lower() or quantities >= 1)
            )

            if not (
                is_table_page
                or is_text_budget_page
                or is_mixed_signal
                or is_loose_edital_page
            ):
                continue

            if best_table >= 18:
                detail = "high"
            elif is_text_budget_page and best_table < min_table_score:
                detail = "high"
            elif best_table >= min_table_score or text_score >= 12:
                detail = "high" if text_score >= 14 or best_table >= 16 else "low"
            else:
                detail = "low"

            priority = best_table * 10 + text_score * 3 + kw_strong * 5 + min(numeric_codes, 10) * 2
            seen_pages.add(page_num)
            # Only accept candidate pages if combined score is reasonably high
            combined_score = best_table + text_score + kw_strong * 2 + min(numeric_codes, 6)
            if combined_score < 20:
                continue

            candidates.append(
                (
                    priority,
                    BudgetPageCandidate(
                        page_number=page_num,
                        image_detail=detail,
                        table_score=best_table,
                        keyword_score=kw_general,
                        text_score=text_score,
                    ),
                )
            )

    if candidates:
        candidates.sort(key=lambda item: item[1].page_number)
        return [item[1] for item in candidates]

    fallback: list[BudgetPageCandidate] = []
    with pdfplumber.open(BytesIO(pdf_content)) as pdf:
        total = min(len(pdf.pages), max_pages)
        for idx in range(total):
            page_num = idx + 1
            if page_num in seen_pages:
                continue
            page = pdf.pages[idx]
            text = page.extract_text() or ""
            if not text.strip():
                continue
            text_score = score_text_budget_likelihood(text)
            if text_score < 5 and not _text_has_budget_numeric_pattern(text):
                continue
            tables = page.extract_tables() or []
            best_table = max(
                (score_budget_table_likelihood(t) for t in tables if t),
                default=0,
            )
            fallback.append(
                BudgetPageCandidate(
                    page_number=page_num,
                    image_detail="high" if text_score >= 10 or best_table >= 14 else "low",
                    table_score=best_table,
                    keyword_score=_keyword_score(text)[0],
                    text_score=text_score,
                )
            )
    return fallback


def detect_readable_pages_for_rescan(
    pdf_content: bytes,
    *,
    max_pages: int = 60,
    min_text_len: int = 40,
) -> List[BudgetPageCandidate]:
    """
    Fallback: páginas com texto legível e algum dígito (varredura ampla).
    """
    pages: List[BudgetPageCandidate] = []
    with pdfplumber.open(BytesIO(pdf_content)) as pdf:
        total = min(len(pdf.pages), max_pages)
        for idx in range(total):
            page_num = idx + 1
            text = (pdf.pages[idx].extract_text() or "").strip()
            if len(text) < min_text_len:
                continue
            if not re.search(r"\d", text):
                continue
            text_score = score_text_budget_likelihood(text)
            pages.append(
                BudgetPageCandidate(
                    page_number=page_num,
                    image_detail="high",
                    table_score=0,
                    keyword_score=_keyword_score(text)[0],
                    text_score=text_score,
                )
            )
    return pages


def merge_page_candidates(
    primary: List[BudgetPageCandidate],
    extra: List[BudgetPageCandidate],
) -> List[BudgetPageCandidate]:
    """Une listas sem duplicar número de página (mantém a de maior text_score)."""
    by_page: dict[int, BudgetPageCandidate] = {}
    for candidate in primary + extra:
        existing = by_page.get(candidate.page_number)
        if existing is None or candidate.text_score > existing.text_score:
            by_page[candidate.page_number] = candidate
    return [by_page[p] for p in sorted(by_page)]
