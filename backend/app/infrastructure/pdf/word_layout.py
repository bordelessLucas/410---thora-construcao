"""
Reconstrução de tabelas orçamentárias por coordenadas de texto (sem OCR).

Pipeline (genérico para diversos layouts de edital):
  1. extract_words → texto + x + y
  2. Agrupar palavras na mesma linha (mesmo Y ± tolerância)
  3. Detectar colunas por papéis semânticos (item/código/banco/desc/und/números)
  4. Atribuir palavras às colunas (borda esquerda)
  5. Normalizar células (unidade+qtd, VU+total colados, % no peso)
  6. Filtrar lixo (rodapé, CREA, telefone…)
  7. Concatenar continuações de descrição

A saída é uma matriz de células compatível com BudgetParser / perfis.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Etapa 6 — linhas inválidas / rodapé
_JUNK_LINE_RE = re.compile(
    "|".join(
        [
            r"p[aá]gina\s*\d+",
            r"\d+\s+de\s+\d+",
            r"\bcrea\b",
            r"engenheiro",
            r"telefone",
            r"\bbancos?\b",
            r"encargos",
            r"cnpj",
            r"compan?hia\s+urbanizadora",
            r"governo\s+do\s+distrito",
            r"diretoria\s+de\s+obras",
            r"respons[aá]vel\s+t[eé]cnico",
            r"valor\s+da\s+obra",
            r"dados\s+da\s+obra",
            r"matr[ií]cula",
            r"@\w+",
            r"novacap\.df",
            r"total\s+(geral|sem\s+bdi|do\s+bdi)\b",
        ]
    ),
    re.IGNORECASE,
)

# Etapa 6 — item hierárquico (ChatGPT: ^\d+(\.\d+)*$)
_ITEM_NUM_RE = re.compile(r"^\d+(?:\.\d+)*$")
_MONEY_RE = re.compile(r"^\d{1,3}(?:\.\d{3})+,\d{2}$|^\d+,\d{2}$")
_NUM_RE = re.compile(r"^[\d.,]+$")
_UNIT_RE = re.compile(
    r"^(m[²2]?|m³|m3|t|kg|un|und|unid\.?|txkm|vb|m[eê]s|ano|cj|conjunto|"
    r"l|lt|h|hh|km|ton|m2|gl|gls|pç|pc|par|jogo)$",
    re.IGNORECASE,
)
_BANK_RE = re.compile(
    r"^(SINAPI|SICRO|SICRO3|SICRO\s*3|Pr[oó]prio|ORSE|TCPO|SEINFRA|CDHU|DER|DNIT)$",
    re.IGNORECASE,
)
_HEADER_HINT_RE = re.compile(
    r"(item|c[oó]digo|descri|quant|valor|total|unid|peso|banco|fonte)",
    re.IGNORECASE,
)


def _cluster_x(values: list[float], *, tol: float, min_support: int) -> list[float]:
    if not values:
        return []
    values = sorted(values)
    clusters: list[dict[str, float | int]] = []
    for x in values:
        if not clusters or x - float(clusters[-1]["mean"]) > tol:
            clusters.append({"sum": x, "n": 1, "mean": x})
        else:
            clusters[-1]["sum"] = float(clusters[-1]["sum"]) + x
            clusters[-1]["n"] = int(clusters[-1]["n"]) + 1
            clusters[-1]["mean"] = float(clusters[-1]["sum"]) / int(clusters[-1]["n"])
    return [float(c["mean"]) for c in clusters if int(c["n"]) >= min_support]


def extract_page_words(page: Any) -> list[dict[str, Any]]:
    """Etapa 1–2: palavras com coordenadas (pdfplumber / pdfminer — sem OCR)."""
    try:
        words = page.extract_words(
            x_tolerance=2,
            y_tolerance=2,
            keep_blank_chars=False,
            use_text_flow=False,
        )
    except Exception as exc:
        logger.debug("extract_words falhou: %s", exc)
        return []
    out: list[dict[str, Any]] = []
    for w in words or []:
        text = str(w.get("text") or "").strip()
        if not text:
            continue
        out.append(
            {
                "text": text,
                "x0": float(w["x0"]),
                "x1": float(w["x1"]),
                "top": float(w["top"]),
                "bottom": float(w.get("bottom") or w["top"]),
            }
        )
    return out


def group_words_into_lines(
    words: list[dict[str, Any]],
    *,
    y_tolerance: float = 2.0,
) -> list[dict[str, Any]]:
    """Etapa 3: agrupa por Y (mesma linha)."""
    ordered = sorted(words, key=lambda w: (w["top"], w["x0"]))
    lines: list[dict[str, Any]] = []
    for w in ordered:
        placed = False
        for line in lines:
            if abs(float(line["y"]) - float(w["top"])) <= y_tolerance:
                line["words"].append(w)
                tops = [float(x["top"]) for x in line["words"]]
                line["y"] = sum(tops) / len(tops)
                placed = True
                break
        if not placed:
            lines.append({"y": float(w["top"]), "words": [w]})
    for line in lines:
        line["words"].sort(key=lambda w: w["x0"])
        line["text"] = " ".join(w["text"] for w in line["words"])
    return sorted(lines, key=lambda line: float(line["y"]))


def _is_item_start(text: str) -> bool:
    if not _ITEM_NUM_RE.match(text):
        return False
    # Telefone / CEP soltos no rodapé (ex.: "92")
    if text.isdigit() and int(text) > 40:
        return False
    return True


def _is_budget_item_line(line: dict[str, Any]) -> bool:
    words = line.get("words") or []
    if not words:
        return False
    return _is_item_start(str(words[0]["text"]))


def _is_header_line(text: str) -> bool:
    low = text.lower()
    hits = len(_HEADER_HINT_RE.findall(low))
    return hits >= 3 and ("item" in low or "descri" in low)


def _is_title_line(text: str) -> bool:
    low = text.lower()
    return any(
        k in low
        for k in (
            "orçamento sintético",
            "orcamento sintetico",
            "planilha orçamentária",
            "planilha orcamentaria",
            "orçamento analítico",
            "orcamento analitico",
        )
    )


def _is_junk_line(text: str) -> bool:
    return bool(_JUNK_LINE_RE.search(text))


def detect_column_boundaries(item_lines: list[dict[str, Any]]) -> list[float]:
    """
    Etapa 4: colunas por papéis semânticos (não depende de um PDF fixo).

    Usa posições X recorrentes de:
      item | código | banco | início da descrição | unidade | números à direita
    """
    item_x: list[float] = []
    code_x: list[float] = []
    bank_x: list[float] = []
    desc_x: list[float] = []
    unit_x: list[float] = []
    num_x: list[float] = []

    for line in item_lines:
        words: list[dict[str, Any]] = line.get("words") or []
        if not words:
            continue
        item_x.append(float(words[0]["x0"]))
        idx = 1
        if (
            idx < len(words)
            and idx + 1 < len(words)
            and _BANK_RE.match(str(words[idx + 1]["text"]))
        ):
            code_x.append(float(words[idx]["x0"]))
            idx += 1
        if idx < len(words) and _BANK_RE.match(str(words[idx]["text"])):
            bank_x.append(float(words[idx]["x0"]))
            idx += 1
        if idx < len(words):
            desc_x.append(float(words[idx]["x0"]))

        for w in words:
            text = str(w["text"])
            if _UNIT_RE.match(text):
                unit_x.append(float(w["x0"]))
            if _MONEY_RE.match(text) or (_NUM_RE.match(text) and float(w["x0"]) > 280):
                num_x.append(float(w["x0"]))

    n = max(1, len(item_lines))
    cols: list[float] = []
    for bucket, tol, support in (
        (item_x, 5.0, max(2, n // 12)),
        (code_x, 8.0, max(2, n // 14)),
        (bank_x, 8.0, max(2, n // 14)),
        (desc_x, 10.0, max(2, n // 12)),
        (unit_x, 10.0, max(2, n // 12)),
        (num_x, 8.0, max(2, n // 10)),
    ):
        cols.extend(_cluster_x(bucket, tol=tol, min_support=support))

    cols = sorted(cols)
    deduped: list[float] = []
    for c in cols:
        if not deduped or c - deduped[-1] > 6:
            deduped.append(c)
    return deduped


def assign_words_to_columns(
    words: list[dict[str, Any]],
    columns: list[float],
    *,
    left_tol: float = 2.0,
) -> list[str]:
    """Atribui cada palavra à coluna de maior start X ≤ x0 (+ tolerância)."""
    if not columns:
        return [" ".join(w["text"] for w in words)]
    cells: list[list[str]] = [[] for _ in columns]
    for w in words:
        idx = 0
        x0 = float(w["x0"])
        for i, cx in enumerate(columns):
            if x0 >= cx - left_tol:
                idx = i
        cells[idx].append(str(w["text"]))
    return [" ".join(parts).strip() for parts in cells]


def normalize_row_cells(cells: list[str]) -> list[str]:
    """Separa unidade+qtd / VU+total colados e une peso + '%'."""
    merged: list[str] = []
    for cell in cells:
        if cell == "%" and merged:
            merged[-1] = f"{merged[-1]} %".strip()
        else:
            merged.append(cell)

    out: list[str] = []
    for cell in merged:
        if not cell:
            out.append(cell)
            continue
        parts = cell.split()
        if len(parts) == 2 and _UNIT_RE.match(parts[0]) and _NUM_RE.match(parts[1]):
            out.extend(parts)
            continue
        if len(parts) >= 2 and all(_NUM_RE.match(p) or _MONEY_RE.match(p) for p in parts):
            out.extend(parts)
            continue
        if len(parts) > 1:
            tail: list[str] = []
            for part in reversed(parts):
                if (_MONEY_RE.match(part) or _NUM_RE.match(part)) and (
                    not tail or _MONEY_RE.match(part) or _NUM_RE.match(part)
                ):
                    if not tail or all(_MONEY_RE.match(t) or _NUM_RE.match(t) for t in tail):
                        tail.append(part)
                        continue
                break
            else:
                if len(tail) >= 2:
                    out.extend(reversed(tail))
                    continue
            if len(tail) >= 2:
                head = parts[: len(parts) - len(tail)]
                if head:
                    out.append(" ".join(head))
                out.extend(reversed(tail))
                continue
        out.append(cell)
    return out


def merge_description_continuations(
    rows: list[list[str]],
    pending_text: str,
) -> None:
    """Etapa 8: se a próxima linha não inicia com código/item, concatena na descrição."""
    if not rows or not pending_text.strip():
        return
    last = rows[-1]
    best_i = 0
    best_len = -1
    for i, cell in enumerate(last):
        text = str(cell or "")
        if not text or _NUM_RE.match(text) or _MONEY_RE.match(text):
            continue
        if "%" in text and len(text) < 16:
            continue
        if len(text) > best_len:
            best_len = len(text)
            best_i = i
    last[best_i] = f"{last[best_i]} {pending_text}".strip()


def lines_to_table_rows(
    lines: list[dict[str, Any]],
    columns: list[float],
) -> list[list[str]]:
    """Monta matriz de células a partir das linhas agrupadas."""
    rows: list[list[str]] = []
    for line in lines:
        text = str(line.get("text") or "")
        words = line.get("words") or []
        if not words:
            continue
        first = str(words[0]["text"])
        is_item = _is_item_start(first)
        is_header = _is_header_line(text)
        is_title = _is_title_line(text)

        if _is_junk_line(text) and not is_item:
            continue

        if rows and not is_item and not is_header and not is_title:
            # Continuação de descrição — ignora se parecer totalização/rodapé
            if _is_junk_line(text):
                continue
            if re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}", text):
                continue
            merge_description_continuations(rows, text)
            continue

        if is_item or is_header or is_title:
            raw = assign_words_to_columns(words, columns)
            rows.append(normalize_row_cells(raw))

    return rows


def extract_table_from_page_words(
    page: Any,
    page_index: int,
    *,
    y_tolerance: float = 2.0,
) -> dict[str, Any] | None:
    """
    Extrai uma tabela orçamentária da página via layout de palavras.

    Retorna dict no formato de table_extract (rows, bbox, table_id) ou None.
    """
    words = extract_page_words(page)
    if len(words) < 8:
        return None

    lines = group_words_into_lines(words, y_tolerance=y_tolerance)
    item_lines = [line for line in lines if _is_budget_item_line(line)]
    if len(item_lines) < 3:
        return None

    columns = detect_column_boundaries(item_lines)
    if len(columns) < 3:
        # Fallback: tokens por gap horizontal (ainda genérico)
        columns = []
        rows: list[list[str]] = []
        for line in lines:
            text = str(line.get("text") or "")
            words_line = line.get("words") or []
            if not words_line:
                continue
            first = str(words_line[0]["text"])
            is_item = _is_item_start(first)
            is_header = _is_header_line(text)
            is_title = _is_title_line(text)
            if _is_junk_line(text) and not is_item:
                continue
            if rows and not is_item and not is_header and not is_title:
                if _is_junk_line(text) or re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}", text):
                    continue
                merge_description_continuations(rows, text)
                continue
            if is_item or is_header or is_title:
                gap_cells: list[list[str]] = [[words_line[0]["text"]]]
                for i in range(1, len(words_line)):
                    prev, cur = words_line[i - 1], words_line[i]
                    if float(cur["x0"]) - float(prev["x1"]) >= 8:
                        gap_cells.append([cur["text"]])
                    else:
                        gap_cells[-1].append(cur["text"])
                rows.append(normalize_row_cells([" ".join(p) for p in gap_cells]))
    else:
        rows = lines_to_table_rows(lines, columns)

    nonempty = sum(1 for row in rows if any(str(c).strip() for c in row))
    if nonempty < 3:
        return None

    xs = [float(w["x0"]) for w in words] + [float(w["x1"]) for w in words]
    ys = [float(w["top"]) for w in words] + [float(w["bottom"]) for w in words]
    bbox = (min(xs), min(ys), max(xs), max(ys))

    logger.debug(
        "word_layout pág %s: %s linhas, %s colunas detectadas, %s item-lines",
        page_index + 1,
        nonempty,
        len(columns),
        len(item_lines),
    )

    return {
        "rows": rows,
        "bbox": bbox,
        "table_id": f"page_{page_index}_word_layout",
        "section_name": None,
        "extraction_method": "word_layout",
        "column_starts": columns,
    }


def extract_document_total_geral(file_path: Any, max_pages: int = 3) -> float | None:
    """
    Procura 'Total Geral' / 'Valor da Obra' nas primeiras páginas (texto nativo).
    Usado na validação financeira (Etapa 10).
    """
    from pathlib import Path

    import pdfplumber

    from app.domain.money import parse_brl

    path = Path(file_path)
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
            r"valor\s+global|total\s+do\s+or[cç]amento)\s*:?\s*R\$\s*([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(
            r"TOTAL:\s*R\$\s*([\d.]+,\d{2})\s+R\$\s*([\d.]+,\d{2})",
            re.IGNORECASE,
        ),
        re.compile(r"TOTAL:\s*R\$\s*([\d.]+,\d{2})", re.IGNORECASE),
    ]
    best: float | None = None
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[: max(max_pages, 8)]:
            text = page.extract_text() or ""
            for pattern in patterns:
                match = pattern.search(text)
                if not match:
                    continue
                groups = [g for g in match.groups() if g]
                values = [v for v in (parse_brl(g) for g in groups) if v >= 1_000]
                if not values:
                    continue
                value = max(values)
                if best is None or value > best:
                    best = value
    return best
