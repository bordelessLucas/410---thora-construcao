"""
Descobre páginas de planilha orçamentária (sintético / executivo).

Evita processar capa, composição auxiliar, cronograma etc. — causa comum
de o parser “se perder” em PDFs multi-página.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import pdfplumber

logger = logging.getLogger(__name__)

_ITEM_HIER_RE = re.compile(r"^\d+(?:\.\d+){1,}$")
_MONEY_BR_RE = re.compile(r"\d{1,3}(?:\.\d{3})+,\d{2}|\d+,\d{2}")
_MONEY_US_RE = re.compile(r"\d+\.\d{2}\b")
_PESO_RE = re.compile(r"\b\d{1,2},\d{2}\s*%|\bpeso\s*\(?%\)?", re.IGNORECASE)

# Títulos fortes: planilha de serviços / sintético / detalhado / estudo de caso
_POSITIVE_TITLE = re.compile(
    r"or[cç]amento\s+sint[eé]tico|or[cç]amento\s+detalhado|"
    r"planilha\s+de\s+servi[cç]os|quadro\s+de\s+pre[cç]os|"
    r"or[cç]amento\s+resumido|resumo\s+do\s+or[cç]amento|"
    r"or[cç]amento\s+de\s+constru|"
    r"estudo\s+de\s+caso|simula[cç][aã]o\s+(?:de\s+)?interven|"
    # "Planilha Orçamentária" genérica, EXCETO analítica
    r"planilha\s+or[cç]ament[aá]ria(?!\s+anal[ií]tica)",
    re.IGNORECASE,
)
_STRONG_POSITIVE = re.compile(
    r"or[cç]amento\s+sint[eé]tico|"
    r"or[cç]amento\s+detalhado|"
    r"estudo\s+de\s+caso|"
    r"simula[cç][aã]o\s+(?:de\s+)?interven",
    re.IGNORECASE,
)
_SINTESE_DETALHADO = re.compile(
    r"s[ií]ntese\s+do\s+or[cç]amento\s+detalhado",
    re.IGNORECASE,
)

# Negativos: só seções que NÃO devem entrar na ABC (não "composição" solto na descrição)
_NEGATIVE_TITLE = re.compile(
    r"composi[cç][aã]o\s+auxiliar|"
    r"composi[cç][aã]o\s+de\s+custo|"
    r"composi[cç][aã]o\s+de\s+pre[cç]o|"
    r"planilha\s+or[cç]ament[aá]ria\s+anal[ií]tica|"
    r"cronograma\s+f[ií]sico|curva\s+abc\s+de\s+servi|"
    r"\bcurva\s+abc\b|"
    r"memorial\s+descritivo|especifica[cç][aã]o\s+t[eé]cnica|"
    r"folha\s+de\s+rosto|quadro\s+de\s+insumos|lista\s+de\s+insumos|"
    r"mem[oó]ria\s+de\s+c[aá]lculo|mapa\s+de\s+cota[cç][aã]o|"
    r"benef[ií]cios\s+e\s+despesas\s+indiretas|"
    r"nota\s+t[eé]cnica|orienta[cç][oõ]es\s+para\s+elabora|"
    # Matriz multi-nível (I/II/III/IV) — não é a planilha sintética base
    r"n[ií]vel\s+de\s+esfor[cç]o\s+i\b|"
    r"n[ií]veis\s+de\s+esfor[cç]o",
    re.IGNORECASE,
)


def score_page_text(text: str, words: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Pontua uma página quanto à chance de ser planilha executiva."""
    low = text or ""
    words = words or []

    hier = 0
    money = 0
    for w in words:
        t = str(w.get("text") or "")
        if _ITEM_HIER_RE.match(t):
            hier += 1
        if _MONEY_BR_RE.fullmatch(t) or (
            _MONEY_US_RE.fullmatch(t) and "," not in t and t.count(".") == 1
        ):
            money += 1

    if not words and low:
        hier = len(re.findall(r"\b\d+(?:\.\d+){1,}\b", low))
        money = len(_MONEY_BR_RE.findall(low)) + len(_MONEY_US_RE.findall(low))

    pos = 1 if _POSITIVE_TITLE.search(low) else 0
    # "Síntese do orçamento detalhado" na capa da praça-modelo ≠ planilha executiva
    strong_hit = _STRONG_POSITIVE.search(low) and not _SINTESE_DETALHADO.search(low)
    strong_pos = 1 if strong_hit else 0
    neg = 1 if _NEGATIVE_TITLE.search(low) else 0
    # Matriz I/II/III/IV: mesmo com cabeçalho "estudo de caso", não é a planilha base
    if re.search(r"n[ií]vel\s+de\s+esfor[cç]o\s+i\b", low, re.I):
        neg = 1
        strong_pos = 0
    peso = 1 if _PESO_RE.search(low) else 0

    banks = len(re.findall(r"\b(?:SINAPI|SICRO|ORSE|TCPO|SEINFRA)\b", low, re.I))

    score = hier * 3 + money + banks * 2
    if strong_pos:
        score += 200
    elif pos:
        score += 120
    if peso and not neg:
        score += 40
    if neg and not pos and not strong_pos:
        score -= 150
    elif neg and (pos or strong_pos):
        score -= 40
    if neg and re.search(r"n[ií]vel\s+de\s+esfor[cç]o\s+i\b", low, re.I):
        score -= 200

    # Semente forte só vale se a página parece planilha (hierarquia / dinheiro)
    strong_seed = bool(strong_pos and (hier >= 15 or money >= 40))

    return {
        "score": score,
        "hier_items": hier,
        "money_tokens": money,
        "positive_title": bool(pos or strong_pos),
        "strong_positive": strong_seed,
        "negative_title": bool(neg),
        "has_peso": bool(peso),
    }


def _is_hard_negative(s: dict[str, Any]) -> bool:
    """Página claramente fora do sintético/detalhado (analítica, curva ABC, níveis)."""
    if s.get("strong_positive"):
        return False
    return bool(s["negative_title"])


def _is_budget_continuation(s: dict[str, Any], *, min_score: int) -> bool:
    """Continuação de planilha: muita hierarquia/dinheiro mesmo sem título."""
    if s["strong_positive"] or (s["positive_title"] and not s["negative_title"]):
        return True
    if _is_hard_negative(s):
        return False
    if s["score"] >= min_score and s["hier_items"] >= 8:
        return True
    if s["hier_items"] >= 20 and s["money_tokens"] >= 20:
        return True
    return False


def _best_contiguous_by_score(scored: list[dict[str, Any]], indices: list[int]) -> list[int]:
    """Escolhe o bloco contíguo com maior soma de score (prioriza sintético/detalhado)."""
    if not indices:
        return []
    indices = sorted(set(indices))
    blocks: list[list[int]] = []
    cur = [indices[0]]
    for i in indices[1:]:
        if i == cur[-1] + 1:
            cur.append(i)
        else:
            blocks.append(cur)
            cur = [i]
    blocks.append(cur)

    def block_key(block: list[int]) -> tuple[int, int, float, int]:
        total = sum(scored[j]["score"] for j in block)
        has_strong = any(scored[j].get("strong_positive") for j in block)
        has_pos = any(scored[j]["positive_title"] for j in block)
        # Prefere sintético/detalhado; depois título positivo; depois score; bloco menor
        return (1 if has_strong else 0, 1 if has_pos else 0, total, -len(block))

    return max(blocks, key=block_key)


def _expand_from_seed(
    scored: list[dict[str, Any]],
    seeds: list[int],
    *,
    min_score: int,
) -> list[int]:
    """Expande cada semente e escolhe o melhor bloco (não une sementes distantes)."""
    if not seeds:
        return []

    def expand_one(seed: int) -> list[int]:
        lo = hi = seed
        while lo > 0 and _is_budget_continuation(scored[lo - 1], min_score=min_score // 2):
            lo -= 1
        while hi + 1 < len(scored) and _is_budget_continuation(
            scored[hi + 1], min_score=min_score // 2
        ):
            hi += 1
        return [
            i
            for i in range(lo, hi + 1)
            if not _is_hard_negative(scored[i]) or scored[i].get("strong_positive")
        ]

    blocks = [expand_one(s) for s in sorted(set(seeds)) if 0 <= s < len(scored)]
    blocks = [b for b in blocks if b]
    if not blocks:
        return []

    def block_key(block: list[int]) -> tuple[int, float, int]:
        total = sum(scored[j]["score"] for j in block)
        has_strong = any(scored[j].get("strong_positive") for j in block)
        # Prefere bloco com sintético/detalhado e maior score; evita blocos gigantes
        return (1 if has_strong else 0, total, -len(block))

    return max(blocks, key=block_key)


def discover_budget_pages(
    pdf_path: Path | str,
    *,
    hint_pages: list[int] | None = None,
    min_score: int = 40,
    max_pages: int = 40,
) -> list[int]:
    """
    Retorna índices 0-based das páginas de planilha orçamentária.

    Estratégia:
      1) pontua todas as páginas
      2) se houver hint (tabelas selecionadas), expande para bloco contíguo vizinho
      3) senão, pega o bloco contíguo de maior score (prioriza sintético/detalhado)
    """
    path = Path(pdf_path)
    scored: list[dict[str, Any]] = []

    with pdfplumber.open(path) as pdf:
        for idx, page in enumerate(pdf.pages):
            try:
                words = page.extract_words(
                    x_tolerance=2, y_tolerance=2, keep_blank_chars=False, use_text_flow=False
                )
            except Exception:
                words = []
            text = ""
            try:
                text = page.extract_text() or ""
            except Exception:
                text = " ".join(str(w.get("text") or "") for w in (words or []))
            meta = score_page_text(text, words)
            meta["page_index"] = idx
            scored.append(meta)

    if not scored:
        return list(hint_pages or [])

    candidates = [
        s["page_index"]
        for s in scored
        if (s["score"] >= min_score or s["positive_title"] or s.get("strong_positive"))
        and not _is_hard_negative(s)
        and (s["hier_items"] >= 8 or s["positive_title"] or s.get("strong_positive"))
    ]

    def _trim_after_hierarchy_drop(indices: list[int]) -> list[int]:
        if not indices:
            return []
        out: list[int] = []
        for i in indices:
            hier = scored[i]["hier_items"]
            if out and _is_hard_negative(scored[i]):
                break
            if out and hier < 5 and scored[i]["negative_title"]:
                break
            if out and hier == 0 and scored[out[-1]]["hier_items"] >= 20:
                break
            # Para ao entrar em planilha analítica / composição auxiliar
            if out and scored[i]["negative_title"] and not scored[i].get("strong_positive"):
                break
            out.append(i)
        return out or indices

    selected: list[int] = []

    # Prioriza sementes com "Orçamento Sintético/Detalhado"
    strong_seeds = [s["page_index"] for s in scored if s.get("strong_positive")]
    if strong_seeds and not hint_pages:
        selected = _expand_from_seed(scored, strong_seeds, min_score=min_score)

    if hint_pages:
        hints = sorted({p for p in hint_pages if 0 <= p < len(scored)})
        if hints:
            selected = _expand_from_seed(scored, hints, min_score=min_score) or hints

    if not selected:
        selected = _best_contiguous_by_score(scored, candidates)
        if selected:
            selected = _expand_from_seed(scored, selected, min_score=min_score)

    # Se o bloco vencedor é majoritariamente composição/analítica, tenta seeds fortes
    if selected:
        neg_ratio = sum(1 for i in selected if _is_hard_negative(scored[i])) / max(
            1, len(selected)
        )
        if neg_ratio >= 0.5 and strong_seeds:
            selected = _expand_from_seed(scored, strong_seeds, min_score=min_score)

    if not selected and hint_pages:
        selected = sorted({p for p in hint_pages if 0 <= p < len(scored)})

    if not selected:
        ranked = sorted(scored, key=lambda s: s["score"], reverse=True)
        top = [
            s["page_index"]
            for s in ranked
            if s["score"] > 0 and not _is_hard_negative(s)
        ][: max(1, min(8, max_pages))]
        selected = sorted(top)

    selected = _trim_after_hierarchy_drop(sorted(selected))[:max_pages]
    logger.info(
        "[page_discovery] pages=%s scores=%s",
        [i + 1 for i in selected],
        {i + 1: scored[i]["score"] for i in selected if i < len(scored)},
    )
    return selected
