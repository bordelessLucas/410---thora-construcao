"""
Engine 2 — Table Reconstruction Engine.

Reconstrói linhas (Y), colunas (X), descrições quebradas e classifica linhas.
Não usa TAB / múltiplos espaços / regex como mecanismo principal de leitura.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.domain.budget_pipeline.models import LineKind, RawBudgetRow, TextElement, TextLine
from app.domain.money import parse_brl, relative_error

logger = logging.getLogger(__name__)

_ITEM_NUM_RE = re.compile(r"^\d+(?:\.\d+)*$")
# BR: 1.234,56 | 12,34  — US: 1234.56 | 35.50
_MONEY_RE = re.compile(
    r"^(?:\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}|\d{1,3}(?:,\d{3})+\.\d{2}|\d+\.\d{2})$"
)
_NUM_RE = re.compile(r"^[\d.,]+$")
_UNIT_RE = re.compile(
    r"^(m[²2]?|m³|m3|t|kg|un|und|unid\.?|txkm|vb|m[eê]s|ano|cj|conjunto|"
    r"l|lt|h|hh|km|ton|tonelada|m2|gl|gls|pç|pc|par|jogo|saco|mil|sc)$",
    re.IGNORECASE,
)
_BANK_RE = re.compile(
    r"^(SINAPI|SICRO|SICRO3|SICRO\s*3|Pr[oó]prio|PR[OÓ]PRIA|ORSE|TCPO|SEINFRA|"
    r"CDHU|DER|DNIT|ANP|URE|EMOP|FDE|SCO|SIURB)$",
    re.IGNORECASE,
)
_HEADER_HINT_RE = re.compile(
    r"(item|c[oó]digo|descri|quant|valor|total|unid|peso|banco|fonte|custo|faixa|incid|acumul)",
    re.IGNORECASE,
)
_CATALOG_CODE_RE = re.compile(
    r"^(?:\d{4,}(?:-\s*[A-Za-zÀ-ÿ0-9._/-]+)?|[A-Za-z]{1,4}\s*\d{2,}(?:[.\-/]\d+)*|\d{4,}-\s*)$"
)
_ABC_HEADER_RE = re.compile(
    r"custo\s+parcial|custo\s+unit|%\s*incid|%\s*acumul|\bfaixa\b",
    re.IGNORECASE,
)
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
            # Parâmetros de transporte / empolamento (não são serviços)
            r"^dist[aâ]ncia\b",
            r"\bdist[aâ]ncia\s+(at[eé]|m[eé]dia|pedreira|canteiro|areal|tomo)",
            r"^peso\s+espec",
            r"^empolamento\b",
            r"^taxa\s+de\s+consumo\b",
            r"^coeficiente\s+de\s+empolamento\b",
            r"obs\.:\s*os\s+coeficientes",
        ]
    ),
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


def group_elements_into_lines(
    elements: list[TextElement],
    *,
    y_tolerance: float = 2.0,
) -> list[TextLine]:
    """Agrupa elementos pelo eixo Y (mesma linha visual)."""
    ordered = sorted(elements, key=lambda e: (e.page, e.y0, e.x0))
    lines: list[TextLine] = []
    for el in ordered:
        placed = False
        for line in lines:
            if line.page != el.page:
                continue
            if abs(line.y - el.y0) <= y_tolerance:
                line.elements.append(el)
                tops = [e.y0 for e in line.elements]
                line.y = sum(tops) / len(tops)
                placed = True
                break
        if not placed:
            lines.append(TextLine(y=el.y0, page=el.page, elements=[el]))
    for line in lines:
        line.elements.sort(key=lambda e: e.x0)
    return sorted(lines, key=lambda line: (line.page, line.y))


def _is_item_start(text: str, *, allow_catalog_code: bool = False) -> bool:
    """
    Aceita hierárquicos (1.1.1) e códigos simples (001) / seções (1..39).
    Com allow_catalog_code (layout Curva ABC): também 93370 / 92394-ADAPTADO.
    Rejeita fragmentos de telefone (61, 92…) sem hierarquia.
    """
    text = (text or "").strip()
    if allow_catalog_code and _CATALOG_CODE_RE.match(text):
        return True
    if not _ITEM_NUM_RE.match(text):
        return False
    if "." in text:
        return True
    if text.isdigit():
        # 001, 002… (planilha simples)
        if len(text) >= 2 and text.startswith("0"):
            return int(text) <= 999
        # Seções / itens rasos típicos de orçamento BR
        return 1 <= int(text) <= 39
    return True


def _page_has_abc_layout(lines: list[TextLine]) -> bool:
    """Detecta cabeçalho de Curva ABC pronta nas primeiras linhas."""
    for line in lines[:30]:
        low = line.text.lower()
        if _ABC_HEADER_RE.search(low) and (
            "codigo" in low
            or "código" in low
            or "descri" in low
        ):
            return True
        hits = len(_HEADER_HINT_RE.findall(low))
        if hits >= 3 and ("custo parcial" in low or ("faixa" in low and "custo" in low)):
            return True
    return False


def _looks_like_noise_row(text: str, cells: list[str] | None = None) -> bool:
    """Rodapé, e-mail, telefone, paginação, parâmetros de distância/peso."""
    blob = text
    if cells:
        blob = " ".join(str(c) for c in cells if c)
    low = blob.lower()

    # Serviço orçamentário real (item + banco/BDI) nunca é ruído de paginação.
    # Evita falso positivo em "CA-50 DE 8 MM" (regex antigo `\d+ de \d+`).
    first = str(cells[0]).strip() if cells else ""
    looks_budget_item = bool(
        (_is_item_start(first) if first else False)
        and (
            _BANK_RE.search(blob)
            or re.search(r"\d{1,2},\d{2}\s*%", blob)
            or re.search(r"R\$\s*\d", blob, re.I)
        )
    )
    if looks_budget_item:
        # Ainda filtra tabela auxiliar de peso/empolamento colada no fim do PDF
        if re.search(
            r"\b(peso\s+espec|empolamento|taxa\s+de\s+consumo|"
            r"coeficiente\s+de\s+empolamento|dimensionamento\s+dos\s+volumes)\b",
            low,
        ):
            return True
        return False

    if "@" in low or "novacap.df" in low:
        return True
    # Paginação: "página 3 de 52" ou linha só com "3 de 52" — não "CA-50 DE 8"
    if re.search(r"\bp[aá]gina\s*\d+", low):
        return True
    if re.search(r"^\s*\d{1,3}\s+de\s+\d{1,3}\s*$", low):
        return True
    if re.search(r"/\s*\w+@", low):
        return True
    if re.search(r"\b\d{2}\s+\d{8,9}\b", blob) and not _MONEY_RE.search(blob.replace(" ", "")):
        return True
    # Tabela de distâncias / pesos específicos / empolamento
    if re.search(
        r"\b(dist[aâ]ncia|peso\s+espec|empolamento|taxa\s+de\s+consumo|"
        r"coeficiente\s+de\s+empolamento|dimensionamento\s+dos\s+volumes)\b",
        low,
    ):
        return True
    # Linhas "13 / 14 / 17" de coeficiente de peso sem estrutura financeira
    if re.search(r"^\s*\d{1,2}\s+peso\b", low) or re.search(r"\bpeso\s+para\s+dimensionamento\b", low):
        return True
    return False


def _looks_numeric_token(text: str) -> bool:
    t = text.strip().replace("R$", "").strip()
    if not t or "%" in t:
        return False
    # Inteiro longo sem decimal: em geral código SINAPI (92398), não qtd/VU.
    # Quantidades inteiras (ex.: 12119) são aceitas depois da unidade — ver parser.
    if re.fullmatch(r"\d{5,}", t):
        return False
    if _MONEY_RE.match(t) or _NUM_RE.match(t):
        return True
    return False


def _looks_qty_integer_token(text: str) -> bool:
    """Quantidade inteira após a unidade (12119 M) — distinta de código de catálogo."""
    t = text.strip().replace("R$", "").strip()
    return bool(re.fullmatch(r"\d{4,7}", t))


def _page_width_hint(elements: list[TextElement]) -> float:
    if not elements:
        return 600.0
    return max(e.x1 for e in elements)


def _is_header_line(text: str) -> bool:
    low = text.lower()
    hits = len(_HEADER_HINT_RE.findall(low))
    if hits >= 3 and (
        "item" in low
        or "descri" in low
        or "custo" in low
        or "código" in low
        or "codigo" in low
    ):
        return True
    return bool(_ABC_HEADER_RE.search(low) and hits >= 2)


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


def _is_budget_item_line(line: TextLine, *, allow_catalog_code: bool = False) -> bool:
    if not line.elements:
        return False
    return _is_item_start(line.elements[0].text, allow_catalog_code=allow_catalog_code)


def detect_column_boundaries(item_lines: list[TextLine]) -> list[float]:
    """Detecta starts de coluna por papéis semânticos (X recorrente)."""
    item_x: list[float] = []
    code_x: list[float] = []
    bank_x: list[float] = []
    desc_x: list[float] = []
    unit_x: list[float] = []
    num_x: list[float] = []

    all_els = [e for line in item_lines for e in line.elements]
    # Limiar adaptativo: números financeiros costumam estar na metade direita
    x_num_floor = _page_width_hint(all_els) * 0.35

    for line in item_lines:
        els = line.elements
        if not els:
            continue
        item_x.append(els[0].x0)
        idx = 1
        if idx < len(els) and idx + 1 < len(els) and _BANK_RE.match(els[idx + 1].text):
            code_x.append(els[idx].x0)
            idx += 1
        if idx < len(els) and _BANK_RE.match(els[idx].text):
            bank_x.append(els[idx].x0)
            idx += 1
        if idx < len(els):
            desc_x.append(els[idx].x0)
        for el in els:
            if _UNIT_RE.match(el.text):
                unit_x.append(el.x0)
            if _MONEY_RE.match(el.text) or (
                _NUM_RE.match(el.text) and el.x0 >= x_num_floor
            ):
                num_x.append(el.x0)

    n = max(1, len(item_lines))
    # Em planilhas pequenas (ex.: 5 linhas), suporte mínimo = 1
    min_sup = 1 if n < 8 else 2
    cols: list[float] = []
    for bucket, tol, support in (
        (item_x, 5.0, max(min_sup, n // 12)),
        (code_x, 8.0, max(min_sup, n // 14)),
        (bank_x, 8.0, max(min_sup, n // 14)),
        (desc_x, 10.0, max(min_sup, n // 12)),
        (unit_x, 10.0, max(min_sup, n // 12)),
        (num_x, 8.0, max(min_sup, n // 10)),
    ):
        cols.extend(_cluster_x(bucket, tol=tol, min_support=support))

    cols = sorted(cols)
    # Em planilhas curtas, colunas muito próximas fundem item+descrição
    min_gap = 12.0 if n < 10 else 6.0
    deduped: list[float] = []
    for c in cols:
        if not deduped or c - deduped[-1] > min_gap:
            deduped.append(c)
    return deduped


def assign_elements_to_columns(
    elements: list[TextElement],
    columns: list[float],
    *,
    left_tol: float = 2.0,
) -> list[str]:
    if not columns:
        return [" ".join(e.text for e in elements)]
    cells: list[list[str]] = [[] for _ in columns]
    for el in elements:
        idx = 0
        for i, cx in enumerate(columns):
            if el.x0 >= cx - left_tol:
                idx = i
        cells[idx].append(el.text)
    return [" ".join(parts).strip() for parts in cells]


def normalize_row_cells(cells: list[str]) -> list[str]:
    """
    Separa unidade+qtd / VU+total colados; une peso + '%'.
    Também desarma células fundidas do layout GDF:
      'R$ 6.112.483,31' → '6.112.483,31'
      '21,20% R$ 7.408.329,77' → '21,20%' + '7.408.329,77'
      '450.549,66 21,20% R$' → '450.549,66' + '21,20%'
    """
    merged: list[str] = []
    for cell in cells:
        if cell == "%" and merged:
            merged[-1] = f"{merged[-1]} %".strip()
        else:
            merged.append(cell)

    expanded: list[str] = []
    for cell in merged:
        if not cell:
            expanded.append(cell)
            continue
        text = str(cell).strip()
        # R$ colado à esquerda do valor
        text = re.sub(r"^R\$\s*", "", text, flags=re.IGNORECASE).strip()
        text = re.sub(r"\s*R\$\s*$", "", text, flags=re.IGNORECASE).strip()

        # '21,20% R$ 7.408.329,77' ou '21,20% 7.408.329,77'
        m = re.match(
            r"^(\d{1,2},\d{2}\s*%)\s*(?:R\$\s*)?([\d.]+,\d{2})$",
            text,
            re.IGNORECASE,
        )
        if m:
            expanded.append(m.group(1).replace(" ", ""))
            expanded.append(m.group(2))
            continue

        # '883.375,49 21,20% R$ 1.070.651,09' (total S/BDI + BDI + total C/BDI)
        m = re.match(
            r"^([\d.]+,\d{2})\s+(\d{1,2},\d{2}\s*%)\s*(?:R\$\s*)?([\d.]+,\d{2})$",
            text,
            re.IGNORECASE,
        )
        if m:
            expanded.append(m.group(1))
            expanded.append(m.group(2).replace(" ", ""))
            expanded.append(m.group(3))
            continue

        # '6.112.483,31 21,20%' ou '6.112.483,31 21,20% R$'
        m = re.match(
            r"^([\d.]+,\d{2})\s+(\d{1,2},\d{2}\s*%)(?:\s*R\$)?$",
            text,
            re.IGNORECASE,
        )
        if m:
            expanded.append(m.group(1))
            expanded.append(m.group(2).replace(" ", ""))
            continue

        # 'R$ 6.112.483,31' já sem R$ → valor puro
        if re.match(r"^[\d.]+,\d{2}$", text):
            expanded.append(text)
            continue

        parts = text.split()
        if len(parts) == 2 and _UNIT_RE.match(parts[0]) and _NUM_RE.match(parts[1]):
            expanded.extend(parts)
            continue
        # '12.119 M' / '12119 M' → quantidade + unidade
        if len(parts) == 2 and (
            _NUM_RE.match(parts[0]) or _MONEY_RE.match(parts[0]) or _looks_qty_integer_token(parts[0])
        ) and _UNIT_RE.match(parts[1]):
            expanded.extend(parts)
            continue
        if len(parts) >= 2 and all(_NUM_RE.match(p) or _MONEY_RE.match(p) for p in parts):
            expanded.extend(parts)
            continue
        if len(parts) > 1:
            tail: list[str] = []
            for part in reversed(parts):
                if _MONEY_RE.match(part) or _NUM_RE.match(part):
                    if not tail or all(_MONEY_RE.match(t) or _NUM_RE.match(t) for t in tail):
                        tail.append(part)
                        continue
                break
            else:
                if len(tail) >= 2:
                    expanded.extend(reversed(tail))
                    continue
            if len(tail) >= 2:
                head = parts[: len(parts) - len(tail)]
                if head:
                    expanded.append(" ".join(head))
                expanded.extend(reversed(tail))
                continue
        expanded.append(text)
    return expanded


def _merge_description_only(row: RawBudgetRow, continuation: str) -> None:
    """Concatena só descrição — nunca valores/códigos/subtotais."""
    if re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}", continuation):
        return
    if _is_junk_line(continuation):
        return
    if not continuation.strip():
        return
    row.descricao = f"{row.descricao} {continuation}".strip()


def _split_leading_item(cell: str) -> tuple[str, str]:
    """Separa '001 Cimento' → ('001', 'Cimento')."""
    text = (cell or "").strip()
    if not text:
        return "", ""
    if _is_item_start(text):
        return text, ""
    match = re.match(r"^(\d+(?:\.\d+)*)\s+(.+)$", text)
    if match and _is_item_start(match.group(1)):
        return match.group(1), match.group(2).strip()
    return "", text


def _normalize_item_cells(cells: list[str]) -> list[str]:
    """Garante que a 1ª célula seja só o nº do item."""
    nonempty = [str(c).strip() for c in cells if str(c).strip()]
    if not nonempty:
        return []
    item, rest = _split_leading_item(nonempty[0])
    if not item:
        return nonempty
    out = [item]
    if rest:
        out.append(rest)
    out.extend(nonempty[1:])
    return out


def _hit_to_row(hit: dict[str, Any], cells: list[str], page: int) -> RawBudgetRow:
    item_numero = str(hit.get("item_numero") or "")
    depth = item_numero.count(".") if item_numero else 0
    codigo = str(hit.get("codigo") or "").strip()
    qty = float(hit.get("quantidade") or 0)
    vu = float(hit.get("valor_unitario") or 0)
    vt = float(hit.get("valor_total") or 0)
    kind: LineKind = "item"
    # Grupo hierárquico: sem código e VU≈total (subtotal de seção)
    if depth <= 1 and not codigo and qty <= 1 and (vu <= 0 or (vt > 0 and abs(vu - vt) < 0.02)):
        kind = "group"
    # Planilha simples 001…: nunca grupo se qtd×VU ≈ total
    if (
        depth == 0
        and item_numero.isdigit()
        and qty > 0
        and vu > 0
        and vt > 0
        and abs(qty * vu - vt) <= max(0.05, vt * 0.02)
    ):
        kind = "item"
    # qty explícita 0 + total 0 → preservar (não inventar 1×VU).
    # qty ausente (0) com total > 0 → default 1 só para item precificado.
    if qty > 0:
        quantidade = qty
    elif vt <= 0:
        quantidade = 0.0
    elif kind == "item" and vu > 0 and abs(vu - vt) > 0.02:
        quantidade = 1.0
    else:
        quantidade = 0.0

    row = RawBudgetRow(
        item_numero=item_numero,
        codigo=codigo,
        banco=str(hit.get("banco") or ""),
        descricao=str(hit.get("descricao") or ""),
        unidade=str(hit.get("unidade") or "un"),
        quantidade=quantidade,
        valor_unitario=vu,
        valor_total=vt,
        bdi=float(hit.get("bdi") or 0),
        page=page,
        kind=kind,
        cells=cells,
    )
    return _mark_completeness(row)


def _resolve_qty_vu_from_moneys(
    moneys: list[float],
    *,
    total_com: float | None = None,
) -> tuple[float, float, float]:
    """
    Desambigua cauda monetária → (quantidade, valor_unitario_sem_bdi, total_com_bdi).

    Layouts comuns:
      [qtd, vu_sem, total_com]
      [qtd, vu_sem, vu_com, total_com]
      [qtd, vu_sem, total_sem, total_com]
      [0, vu_sem, vu_com, 0]  → linha zerada (preservar qty=0, total=0)

    Nunca promove VU→quantidade. Nunca inventa total a partir de VU quando qty=0.
    """
    raw = [float(v) for v in moneys]
    if not raw:
        return 0.0, 0.0, float(total_com or 0.0)

    # Total explícito (último valor ou total_com passado)
    if total_com is not None and total_com >= 0:
        total = float(total_com)
    else:
        total = raw[-1]

    # Linha zerada do PDF: quantidade 0 e total 0 (VU pode existir para referência)
    if len(raw) >= 2 and raw[0] == 0.0 and total == 0.0:
        vu = next((v for v in raw[1:-1] if v > 0), (raw[1] if len(raw) > 1 and raw[1] > 0 else 0.0))
        return 0.0, vu, 0.0

    # Remove zeros internos só para desambiguação de colunas positivas,
    # mas se a 1ª coluna era 0 e o total > 0, qty permanece 0 (não reconstruir).
    leading_zero_qty = len(raw) >= 2 and raw[0] == 0.0
    vals = [float(v) for v in raw if float(v) > 0]
    if not vals:
        return 0.0, 0.0, float(total if total > 0 else 0.0)

    if total <= 0:
        total = vals[-1]
    candidates = vals[:-1] if abs(vals[-1] - total) <= max(0.01, abs(total) * 1e-9) else vals

    # Qty explícita zero + total positivo residual: não inventar qty = total/vu
    if leading_zero_qty:
        vu = candidates[0] if candidates else 0.0
        return 0.0, vu, total if total > 0 else 0.0

    def _ok(q: float, vu: float, tgt: float, tol: float = 0.03) -> bool:
        if q <= 0 or vu <= 0 or tgt <= 0:
            return False
        return relative_error(q * vu, tgt) <= tol

    # 1) qtd × vu_com ≈ total  (preferir quando há 2+ candidatos antes do total)
    if len(candidates) >= 2:
        q = candidates[0]
        # [qtd, vu_sem, vu_com] — testa qtd×último ≈ total
        vu_com = candidates[-1]
        if _ok(q, vu_com, total):
            if len(candidates) >= 3 and _ok(q, candidates[1], candidates[2]):
                return q, candidates[1], total
            if len(candidates) >= 2 and candidates[1] > 0 and candidates[1] <= vu_com:
                return q, candidates[1], total
            return q, vu_com, total

        # [qtd, vu_sem, total_sem] antes do total_com
        if len(candidates) >= 3 and _ok(q, candidates[1], candidates[2]):
            return q, candidates[1], total

        # [qtd, vu_sem] — qtd×vu_sem ≈ total (sem BDI ou BDI embutido no total)
        if _ok(q, candidates[1], total):
            return q, candidates[1], total

        # qtd×vu_sem × fator BDI típico (até ~40%) ≈ total
        if q > 0 and candidates[1] > 0:
            base = q * candidates[1]
            if base > 0 and total > base * 0.98 and total / base <= 1.45:
                return q, candidates[1], total

        # Fallback: 1º candidato é VU e qty = total/vu_com (só se 1º NÃO parece quantidade
        # e o 2º×algum fator fecha). Evita qty=51,43 quando faltou 12119 na lista.
        if len(candidates) >= 2:
            a, b = candidates[0], candidates[1]
            # a parece preço unitário (pequeno) e total/b ou total/a parece quantidade
            for vu_try in (b, a):
                if vu_try <= 0 or vu_try > 10_000:
                    continue
                q_try = total / vu_try
                if q_try >= 10 and _ok(q_try, vu_try, total, tol=0.02):
                    # Preferir interpretação em que o outro valor é vu_sem (~ ≤ vu_com)
                    other = a if vu_try == b else b
                    if other > 0 and other <= vu_try * 1.001:
                        return q_try, other, total
                    return q_try, vu_try, total

    if len(candidates) == 1:
        mid = candidates[0]
        if mid > 0 and abs(mid - total) > 0.01:
            ratio = total / mid if mid else 0.0
            if mid < 10_000 and 0.5 < ratio < 1_000_000 and relative_error(mid * ratio, total) <= 0.02:
                if abs(ratio - round(ratio)) < 0.02 or ratio > mid:
                    if mid < ratio or mid < 5_000:
                        return ratio, mid, total
                return mid, ratio, total
        # Um único valor positivo + total igual → não inventar qty=1
        if abs(mid - total) <= max(0.01, abs(total) * 1e-9):
            return 0.0, mid, 0.0 if total == mid and leading_zero_qty else total
        return 0.0, mid, total if total > 0 else 0.0

    if len(candidates) >= 2:
        # Fallback seguro: 1º = qtd, 2º = VU (não inverter)
        q, vu = candidates[0], candidates[1]
        if q > 0 and vu > 0:
            return q, vu, total

    return 0.0, 0.0, total if total > 0 else 0.0


def _parse_semantic_row(cells: list[str], page: int) -> RawBudgetRow | None:
    """
    Parser genérico direita→esquerda, independente de SINAPI/NOVACAP.

    Layout GDF (Orçamento Detalhado):
      item | fonte | código | desc | und | qtd | custo unit | total S/BDI | BDI% | total C/BDI
    Prefere sempre o total COM BDI (valor após o último %).
    """
    nonempty = _normalize_item_cells(cells)
    if not nonempty or not _is_item_start(nonempty[0]):
        return None

    item_numero = nonempty[0]

    # Unidade cedo: permite aceitar quantidade inteira (12119) após ela,
    # sem confundir com código SINAPI (92398) antes da unidade.
    unit_i_early = None
    for j, c in enumerate(nonempty):
        if j == 0:
            continue
        tokens = c.split()
        if _UNIT_RE.match(c) or (tokens and _UNIT_RE.match(tokens[-1])):
            unit_i_early = j
            break

    # Localiza BDI% e extrai dinheiro da cauda
    bdi_idx = -1
    bdi_pct = 0.0
    for i, c in enumerate(nonempty):
        if re.fullmatch(r"\d{1,2},\d{2}\s*%", c.strip()):
            bdi_idx = i
            bdi_pct = parse_brl(c)

    money_idxs: list[int] = []
    for i, c in enumerate(nonempty):
        if i == 0:
            continue
        if _UNIT_RE.match(c) or _BANK_RE.match(c):
            continue
        if re.fullmatch(r"\d{1,2},\d{2}\s*%", c.strip()):
            continue
        if "%" in c:
            # Tenta extrair dinheiro embutido após %
            m = re.search(r"([\d.]+,\d{2})\s*$", c)
            if m:
                nonempty[i] = m.group(1)
                money_idxs.append(i)
            continue
        if _looks_numeric_token(c):
            money_idxs.append(i)
            continue
        # Quantidade inteira após unidade (ex.: 12119 M) — não é código de catálogo
        if (
            unit_i_early is not None
            and i > unit_i_early
            and _looks_qty_integer_token(c)
        ):
            money_idxs.append(i)

    if not money_idxs:
        return None

    # Se há BDI%, o total C/BDI é o último dinheiro DEPOIS do %; senão, o último dinheiro
    after_bdi = [i for i in money_idxs if bdi_idx >= 0 and i > bdi_idx]
    before_bdi = [i for i in money_idxs if bdi_idx < 0 or i < bdi_idx]

    if after_bdi:
        total_i = after_bdi[-1]
        valor_total = parse_brl(nonempty[total_i])
        before_vals = [parse_brl(nonempty[i]) for i in before_bdi]
        quantidade, valor_unitario, valor_total = _resolve_qty_vu_from_moneys(
            before_vals + [valor_total],
            total_com=valor_total,
        )
        num_idxs = before_bdi + after_bdi
    else:
        num_idxs = money_idxs
        money_vals = [parse_brl(nonempty[i]) for i in num_idxs]
        quantidade, valor_unitario, valor_total = _resolve_qty_vu_from_moneys(money_vals)

    if valor_total < 0:
        return None
    # Linha zerada (qty=0, total=0, VU opcional) permanece para auditoria — fora da ABC
    if valor_total <= 0 and quantidade <= 0 and valor_unitario <= 0:
        return None

    # Unidade — só tokens reconhecidos (não roubar palavras da descrição)
    unidade = "un"
    first_num_i = num_idxs[0] if num_idxs else len(nonempty) - 1
    for j in range(first_num_i - 1, 0, -1):
        c = nonempty[j]
        # 'CA-50 Tonelada' → tenta último token
        tokens = c.split()
        if tokens and _UNIT_RE.match(tokens[-1]):
            unidade = tokens[-1]
            break
        if _UNIT_RE.match(c):
            unidade = c
            break

    # Fonte (banco) + código: layout GDF = Item | Fonte | Código | Desc
    codigo = ""
    banco = ""
    desc_start = 1
    if len(nonempty) > 1 and _BANK_RE.match(nonempty[1].split()[0] if nonempty[1] else ""):
        parts = nonempty[1].split()
        banco = parts[0]
        if len(parts) > 1 and len(parts[1]) <= 24:
            codigo = parts[1]
            desc_start = 2
            if len(nonempty) > 2 and not _looks_numeric_token(nonempty[2]) and not _UNIT_RE.match(nonempty[2]):
                if re.match(r"^[A-Za-z0-9./\-]+$", nonempty[2]) and len(nonempty[2]) <= 24:
                    if not re.search(r"[a-záéíóúãõç ]{6,}", nonempty[2], re.I):
                        codigo = f"{codigo} {nonempty[2]}".strip() if codigo else nonempty[2]
                        desc_start = 3
        else:
            desc_start = 2
            if len(nonempty) > 2 and not _UNIT_RE.match(nonempty[2]) and not _looks_numeric_token(nonempty[2]):
                tok = nonempty[2].split()[0]
                if len(tok) <= 16 and re.match(r"^[A-Za-z0-9./\-]+$", tok):
                    codigo = tok
                    rest = " ".join(nonempty[2].split()[1:])
                    if rest:
                        nonempty[2] = rest
                        desc_start = 2
                    else:
                        desc_start = 3
    elif (
        len(nonempty) > 2
        and not _looks_numeric_token(nonempty[1])
        and not _UNIT_RE.match(nonempty[1])
        and len(nonempty[1]) <= 24
        and re.match(r"^[A-Za-z0-9./\-]+$", nonempty[1])
        and not re.search(r"[a-záéíóúãõç]{4,}", nonempty[1], re.I)
    ):
        codigo = nonempty[1]
        desc_start = 2

    unit_i = None
    for j, c in enumerate(nonempty):
        if j < desc_start:
            continue
        tokens = c.split()
        if _UNIT_RE.match(c) or (tokens and _UNIT_RE.match(tokens[-1])):
            unit_i = j
            break
    desc_end = unit_i if unit_i is not None else first_num_i
    desc_parts: list[str] = []
    for j in range(desc_start, desc_end):
        part = nonempty[j]
        tokens = part.split()
        if tokens and _UNIT_RE.match(tokens[-1]) and len(tokens) > 1:
            desc_parts.append(" ".join(tokens[:-1]))
        else:
            desc_parts.append(part)
    descricao = " ".join(desc_parts).strip()
    descricao = re.sub(r"\bR\$\b", "", descricao).strip()
    descricao = re.sub(r"\s+", " ", descricao)
    if not descricao or len(descricao) < 2:
        # Fallback: junta tudo entre item e o 1º número
        descricao = " ".join(
            c for c in nonempty[1:first_num_i]
            if not _looks_numeric_token(c) and not _UNIT_RE.match(c)
        ).strip()
    if not descricao or len(descricao) < 2:
        return None

    depth = item_numero.count(".")
    is_group = (
        depth <= 1
        and not codigo
        and quantidade <= 1
        and (valor_unitario <= 0 or abs(valor_unitario - valor_total) < 0.01)
        and unidade in {"un", ""}
        and len(num_idxs) <= 2
        and bdi_idx < 0
    )
    if depth == 0 and quantidade > 0 and valor_unitario > 0 and abs(
        quantidade * valor_unitario - valor_total
    ) <= max(0.05, valor_total * 0.02):
        is_group = False

    hit = {
        "item_numero": item_numero,
        "codigo": codigo,
        "banco": banco,
        "descricao": descricao,
        "unidade": unidade or "un",
        "quantidade": 0.0 if is_group else quantidade,
        "valor_unitario": 0.0 if is_group else valor_unitario,
        "valor_total": valor_total,
        "bdi": bdi_pct if bdi_pct > 0 else 0.0,
    }
    if is_group:
        row = RawBudgetRow(
            item_numero=item_numero,
            descricao=descricao,
            valor_total=valor_total,
            page=page,
            kind="group",
            cells=cells,
        )
        return _mark_completeness(row)
    return _hit_to_row(hit, cells, page)


def _parse_cells_to_row(cells: list[str], page: int) -> RawBudgetRow:
    """Interpreta células já alinhadas (ordem visual esquerda→direita)."""
    from budget_parser import BudgetParser

    parser = BudgetParser()
    nonempty = _normalize_item_cells(cells)
    has_bdi_pct = any(re.fullmatch(r"\d{1,2},\d{2}\s*%", str(c).strip()) for c in nonempty)

    # Layout com BDI% explícito: parser semântico (C/BDI) é fonte da verdade
    if has_bdi_pct:
        semantic = _parse_semantic_row(cells, page)
        if semantic and semantic.kind in {"item", "group"} and semantic.valor_total > 0:
            return semantic

    hit = parser.try_parse_sintetico_row(nonempty) or parser.try_parse_novacap_row(nonempty)
    if hit:
        row = _hit_to_row(hit, cells, page)
        if row.kind == "incomplete" or row.valor_total <= 0:
            semantic = _parse_semantic_row(cells, page)
            if semantic and semantic.kind in {"item", "group"}:
                return semantic
        return row

    semantic = _parse_semantic_row(cells, page)
    if semantic:
        return semantic

    first = nonempty[0] if nonempty else ""
    if _is_item_start(first):
        return RawBudgetRow(
            item_numero=first,
            descricao=" ".join(nonempty[1:2]),
            page=page,
            kind="incomplete",
            cells=cells,
            incomplete_reason="parser não reconheceu colunas financeiras",
        )

    return RawBudgetRow(page=page, kind="unknown", cells=cells)


def _mark_completeness(row: RawBudgetRow) -> RawBudgetRow:
    """
    Linha executiva válida: item_numero + descrição + unidade + qtd + VU + total.
    Código de catálogo é preferível; ausência não invalida se o financeiro fecha.
    Grupos (hierarquia) podem não ter código — ficam kind=group.
    """
    if row.kind == "group":
        if not row.descricao or row.valor_total <= 0:
            row.kind = "incomplete"
            row.incomplete_reason = "grupo sem descrição ou total"
        return row

    missing: list[str] = []
    if not row.item_numero:
        missing.append("item_numero")
    if not row.descricao or len(row.descricao) < 3:
        missing.append("descricao")
    if not row.unidade:
        missing.append("unidade")
    if row.quantidade <= 0:
        missing.append("quantidade")
    if row.valor_unitario <= 0:
        missing.append("valor_unitario")
    if row.valor_total <= 0:
        missing.append("valor_total")

    if missing:
        row.kind = "incomplete"
        row.incomplete_reason = "faltam: " + ", ".join(missing)
        return row

    row.kind = "item"
    row.incomplete_reason = ""
    return row


def reconstruct_tables(
    elements: list[TextElement],
    *,
    y_tolerance: float = 2.0,
) -> tuple[list[RawBudgetRow], list[RawBudgetRow], dict[str, Any]]:
    """
    Reconstrói linhas de orçamento a partir de elementos posicionados.

    Retorna (rows_validas_e_grupos, incomplete, meta).
    """
    lines = group_elements_into_lines(elements, y_tolerance=y_tolerance)
    abc_layout = _page_has_abc_layout(lines)
    item_lines = [
        line
        for line in lines
        if _is_budget_item_line(line, allow_catalog_code=abc_layout)
    ]
    columns = detect_column_boundaries(item_lines) if len(item_lines) >= 3 else []

    rows: list[RawBudgetRow] = []
    incomplete: list[RawBudgetRow] = []
    discard_reasons: dict[str, int] = {}

    for line in lines:
        text = line.text
        if not line.elements:
            continue
        first = line.elements[0].text
        is_item = _is_item_start(first, allow_catalog_code=abc_layout)
        is_header = _is_header_line(text)
        is_title = _is_title_line(text)

        if _is_junk_line(text) and not is_item:
            line.kind = "junk"
            discard_reasons["junk"] = discard_reasons.get("junk", 0) + 1
            continue

        if rows and not is_item and not is_header and not is_title:
            if _is_junk_line(text) or re.search(r"\d{1,3}(?:\.\d{3})+,\d{2}", text):
                continue
            # Continuação: só descrição / sufixo de código da última linha
            last = rows[-1] if rows else None
            if last and last.kind in {"item", "group", "incomplete"}:
                # Merge código partido: "92394-" + "ADAPTADO"
                tok = first.strip()
                if last.codigo.endswith("-") and tok and not _looks_numeric_token(tok):
                    last.codigo = f"{last.codigo}{tok}".replace("--", "-")
                    if last.item_numero.endswith("-"):
                        last.item_numero = last.codigo
                else:
                    _merge_description_only(last, text)
            continue

        if is_header or is_title:
            line.kind = "header" if is_header else "title"
            continue

        if not is_item:
            discard_reasons["no_item_start"] = discard_reasons.get("no_item_start", 0) + 1
            continue

        if columns:
            cells = normalize_row_cells(assign_elements_to_columns(line.elements, columns))
        else:
            # Fallback gap-token
            gap_cells: list[list[str]] = [[line.elements[0].text]]
            for i in range(1, len(line.elements)):
                prev, cur = line.elements[i - 1], line.elements[i]
                if cur.x0 - prev.x1 >= 8:
                    gap_cells.append([cur.text])
                else:
                    gap_cells[-1].append(cur.text)
            cells = normalize_row_cells([" ".join(p) for p in gap_cells])

        if _looks_like_noise_row(text, cells):
            line.kind = "junk"
            discard_reasons["noise"] = discard_reasons.get("noise", 0) + 1
            continue

        row = _parse_cells_to_row(cells, page=line.page)
        # Layout ABC: 1ª coluna é código de catálogo, não item hierárquico
        if abc_layout and _CATALOG_CODE_RE.match(first.strip()):
            code = first.strip()
            if not row.codigo or row.codigo == row.item_numero:
                row.codigo = code
            if not row.item_numero or row.item_numero == code or not _ITEM_NUM_RE.match(
                row.item_numero
            ):
                row.item_numero = code
            if row.valor_total > 0 and row.descricao and len(row.descricao) >= 3:
                row.kind = "item"
                row.incomplete_reason = ""
        if row.kind == "incomplete":
            incomplete.append(row)
            discard_reasons["incomplete"] = discard_reasons.get("incomplete", 0) + 1
            continue
        if row.kind in {"item", "group"}:
            rows.append(row)

    # Folhas com código+financeiro mas marcadas incomplete só por regra estrita:
    # reabilita itens X.Y.Z com todos os campos financeiros mesmo sem "código" se
    # a 2ª célula parece código (já parseado). Já tratado em _mark_completeness.

    # Regras do plano: nunca gerar JSON econômico com incompletas.
    # Mas NOVACAP sintético tem folhas com código — golden precisa delas.
    # Itens kind=item com valor_total>0 e descricao entram.
    economic = [r for r in rows if r.kind in {"item", "group"} and r.valor_total > 0]

    # Promove incomplete que na verdade são executivos (financeiro completo)
    recovered: list[RawBudgetRow] = []
    still_incomplete: list[RawBudgetRow] = []
    for row in incomplete:
        if (
            row.item_numero
            and row.descricao
            and row.quantidade > 0
            and row.valor_unitario > 0
            and row.valor_total > 0
        ):
            row.kind = "item"
            row.incomplete_reason = ""
            recovered.append(row)
        elif (
            abc_layout
            and row.descricao
            and row.valor_total > 0
            and (row.codigo or row.item_numero)
        ):
            row.kind = "item"
            row.incomplete_reason = ""
            if not row.codigo and row.item_numero:
                row.codigo = row.item_numero
            recovered.append(row)
        else:
            still_incomplete.append(row)
    economic.extend(recovered)

    meta = {
        "engine": "table_reconstruction",
        "lines_total": len(lines),
        "item_lines": len(item_lines),
        "columns": [round(c, 1) for c in columns],
        "economic_rows": len(economic),
        "incomplete_rows": len(still_incomplete),
        "y_tolerance": y_tolerance,
        "abc_layout": abc_layout,
        "discard_reasons": discard_reasons,
    }
    logger.info(
        "[engine2] linhas=%s item_lines=%s cols=%s econômicos=%s incompletos=%s abc=%s discard=%s",
        len(lines),
        len(item_lines),
        len(columns),
        len(economic),
        len(still_incomplete),
        abc_layout,
        discard_reasons,
    )
    return economic, still_incomplete, meta
