"""
Extração de itens orçamentários a partir de tabelas selecionadas.

Fonte de verdade: pipeline de 5 engines (coordenadas → validação → ABC).
OpenAI, se configurada, só gera insights sobre o JSON já validado.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from budget_parser import BudgetParser
from config import is_openai_configured
from fastapi import HTTPException

from app.domain.abc_curve import (
    build_abc_summary,
    enrich_item_pricing_and_type,
    infer_tipo_linha,
    normalize_item_numero,
)
from app.domain.money import parse_brl, sanitize_bdi_percent
from app.domain.profiles import match_profile, parse_rows_with_profile
from app.infrastructure.storage.table_cache_store import TableCacheStore
from app.infrastructure.storage.upload_store import UploadStore

logger = logging.getLogger(__name__)

_SUBTOTAL_KEYWORDS = (
    "total geral",
    "subtotal",
    "total do grupo",
    "total:",
    "suma",
    "grand total",
)

_COMPOSICAO_TABLE_HINTS = (
    "composição",
    "composicao",
    "composição auxiliar",
    "composicao auxiliar",
    "insumo",
    "coeficiente",
    "custo horário",
    "custo horario",
)

_SINTETICO_TABLE_HINTS = (
    "orçamento sintético",
    "orcamento sintetico",
    "sintético",
    "sintetico",
)

_ANALITICO_EXEC_HINTS = (
    "planilha orçamentária analítica",
    "planilha orcamentaria analitica",
    "orçamento analítico",
    "orcamento analitico",
)


def _rows_preview_text(rows: list[list[Any]], limit: int = 8) -> str:
    parts: list[str] = []
    for row in rows[:limit]:
        parts.append(" ".join(str(c) for c in row if str(c).strip()))
    return " ".join(parts).lower()


def classify_table_kind(
    rows: list[list[Any]],
    *,
    table_name: str = "",
) -> str:
    """
    Classifica tabela para Curva ABC via registry de perfis.
    Fallback legado mantém comportamento se o match for fraco/desconhecido.
    """
    match = match_profile(rows, table_name=table_name, min_confidence=0.2)
    if match.confidence >= 0.2 or match.table_kind in {
        "sintetico",
        "composicao",
        "analitico",
    }:
        return match.table_kind

    # Fallback legado (compatibilidade)
    name = (table_name or "").lower()
    preview = _rows_preview_text(rows)
    blob = f"{name} {preview}"

    if any(h in blob for h in _SINTETICO_TABLE_HINTS):
        return "sintetico"
    if any(h in blob for h in _ANALITICO_EXEC_HINTS):
        return "analitico"
    if any(h in blob for h in _COMPOSICAO_TABLE_HINTS):
        return "composicao"

    xyz = 0
    peso_rows = 0
    comp_first_col = 0
    for row in rows[:20]:
        cells = [str(c).strip() for c in row if str(c).strip()]
        if not cells:
            continue
        first = cells[0].lower()
        if first.startswith("composi") or first == "insumo":
            comp_first_col += 1
        if re.match(r"^\d+\.\d+\.\d+", cells[0]):
            xyz += 1
        if any("%" in c and len(c) < 16 for c in cells[-3:]):
            peso_rows += 1
    if comp_first_col >= 3:
        return "composicao"
    if xyz >= 3 and peso_rows >= 3:
        return "sintetico"

    for row in rows[:5]:
        header_extra = " ".join(str(c).lower() for c in row)
        if "item" in header_extra and "descri" in header_extra and "total" in header_extra:
            if "tipo" in header_extra and "porcent" in header_extra:
                return "composicao"
            if "valor unit" in header_extra or "peso" in header_extra:
                return "orcamento"

    return "orcamento"


def _item_numero(item: dict[str, Any]) -> str:
    return normalize_item_numero(item.get("item_numero") or item.get("item") or "")


def _is_executive_leaf_number(item_numero: str) -> bool:
    return bool(re.match(r"^\d+\.\d+\.\d+", item_numero))


def _is_hierarchy_leaf(item_numero: str, all_numbers: set[str]) -> bool:
    """True se nenhum outro item é filho (prefixo item.)."""
    if not item_numero:
        return True
    prefix = item_numero + "."
    return not any(n.startswith(prefix) for n in all_numbers)


def _select_items_for_curva_abc(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Regras econômicas da Curva ABC:
    1) Se houver Orçamento Sintético, usa só ele.
    2) Descarta composição/analítico.
    3) Se houver numeração hierárquica, usa só folhas (sem filhos) — evita grupo+filho.
    """
    enriched = [enrich_item_pricing_and_type(i) for i in items]

    sintetico = [
        i
        for i in enriched
        if str(i.get("_table_kind") or "") == "sintetico"
        or "sintético" in str(i.get("_source_table_name") or "").lower()
        or "sintetico" in str(i.get("_source_table_name") or "").lower()
    ]
    pool = sintetico if sintetico else [
        i
        for i in enriched
        if str(i.get("_table_kind") or "") not in {"composicao", "analitico"}
    ]
    if not pool:
        pool = enriched

    candidates: list[dict[str, Any]] = []
    for item in pool:
        tipo = str(item.get("tipo_linha") or item.get("tipo") or "").lower()
        desc = str(item.get("descricao") or "").lower()
        kind = str(item.get("_table_kind") or "")
        if tipo == "grupo" or "total do grupo" in desc:
            continue
        if tipo == "composicao" or kind in {"composicao", "analitico"}:
            continue
        if item.get("quarentena") is True:
            continue
        vt = _coerce_number(item.get("valor_total_com_bdi") or item.get("valor_total"))
        if vt <= 0:
            continue
        qty = _coerce_number(item.get("quantidade"))
        if qty >= 1_000_000 and vt < qty:
            continue
        candidates.append(item)

    # Usa TODOS os números do pool (inclui grupos) para detectar folhas —
    # evita contar 1.2 + 1.2.1 quando o pai veio tipado como item.
    all_numbers = {_item_numero(i) for i in pool if _item_numero(i)}
    candidate_numbers = {_item_numero(i) for i in candidates if _item_numero(i)}
    numbers = all_numbers | candidate_numbers
    has_hierarchy = any("." in n for n in numbers)
    if has_hierarchy:
        leaves = [
            i
            for i in candidates
            if _is_hierarchy_leaf(_item_numero(i), numbers)
        ]
        if leaves:
            return leaves

    return candidates


def _coerce_number(value: Any) -> float:
    return parse_brl(value)


def _coerce_bdi(value: Any) -> float:
    return sanitize_bdi_percent(value)


def count_nonempty_rows(rows: list[list[Any]]) -> int:
    return sum(1 for row in rows if any(str(cell).strip() for cell in row))


def _rows_likely_missing_prices(rows: list[list[Any]]) -> bool:
    empty_price_rows = 0
    priced_rows = 0
    for row in rows[1:25]:
        if not any(str(c).strip() for c in row):
            continue
        nums = [_coerce_number(c) for c in row if str(c).strip()]
        if any(n > 0 for n in nums[-2:]):
            priced_rows += 1
        elif len(nums) >= 2:
            empty_price_rows += 1
    return empty_price_rows >= 2 and priced_rows == 0


def _infer_tipo_linha(
    descricao: str,
    quantidade: float,
    valor_unitario: float,
    valor_total: float,
    codigo: str,
    item_numero: str = "",
) -> str:
    return infer_tipo_linha(
        descricao=descricao,
        quantidade=quantidade,
        valor_unitario=valor_unitario,
        valor_total=valor_total,
        codigo=codigo,
        item_numero=item_numero,
    )


def _score_item_confidence(item: dict[str, Any]) -> tuple[float, list[str]]:
    alerts: list[str] = []
    score = 1.0
    quantidade = _coerce_number(item.get("quantidade"))
    valor_unitario = _coerce_number(
        item.get("valor_unitario_com_bdi") or item.get("valor_unitario")
    )
    valor_total = _coerce_number(
        item.get("valor_total_com_bdi") or item.get("valor_total")
    )
    descricao = str(item.get("descricao") or "").strip()
    codigo = str(item.get("codigo") or "").strip()

    if not descricao:
        score -= 0.35
        alerts.append("Descrição ausente")
    if not codigo:
        score -= 0.05
    if quantidade <= 0 and valor_unitario <= 0 and valor_total <= 0:
        score -= 0.4
        alerts.append("Sem quantidade nem preços")
    elif valor_unitario <= 0 and valor_total <= 0:
        score -= 0.2
        alerts.append("Preços ausentes — preencha manualmente")

    if quantidade > 0 and valor_unitario > 0 and valor_total > 0:
        esperado = quantidade * valor_unitario
        erro = abs(valor_total - esperado) / max(abs(valor_total), abs(esperado), 1.0)
        if erro > 0.02:
            score -= min(0.3, erro)
            alerts.append("Qtd×VU pode divergir do total")

    if item.get("quarentena"):
        score = min(score, 0.45)

    return max(0.0, min(1.0, round(score, 3))), alerts


def _parser_row_to_structured(
    raw: dict[str, Any],
    *,
    page: int,
    table_id: str,
    index: int,
    template_sem_precos: bool,
) -> dict[str, Any]:
    descricao = str(raw.get("descricao") or "").strip()
    codigo = str(raw.get("codigo") or "").strip()
    item_numero = str(raw.get("item_numero") or raw.get("item") or "").strip()
    banco = str(raw.get("banco") or "").strip()

    row = enrich_item_pricing_and_type(
        {
            "item": item_numero or str(index),
            "item_numero": item_numero or str(index),
            "banco": banco,
            "codigo": codigo,
            "descricao": descricao,
            "unidade": str(raw.get("unidade") or "un").strip() or "un",
            "quantidade": raw.get("quantidade"),
            "valor_unitario": raw.get("valor_unitario"),
            "valor_total": raw.get("valor_total"),
            "bdi": raw.get("bdi"),
            "origem_extracao": "parser_local",
            "_source_table_id": table_id,
            "_source_page": page,
        }
    )
    for key in ("doc_percentual", "doc_acumulado", "doc_faixa"):
        if raw.get(key) is not None and raw.get(key) != "":
            row[key] = raw[key]

    confianca, alertas = _score_item_confidence(row)
    existing = list(row.get("alertas") or [])
    for a in alertas:
        if a not in existing:
            existing.append(a)
    row["alertas"] = existing
    row["confianca"] = min(float(row.get("confianca") or 1.0), confianca)
    if template_sem_precos and row["valor_unitario"] <= 0 and row["valor_total"] <= 0:
        if "Preços em branco no edital — informe manualmente" not in row["alertas"]:
            row["alertas"].append("Preços em branco no edital — informe manualmente")

    return row


def _items_from_table_rows(
    rows: list[list[Any]],
    *,
    page: int,
    table_id: str,
    table_name: str = "",
    profile_id: str | None = None,
) -> list[dict[str, Any]]:
    parsed_items, profile_match = parse_rows_with_profile(
        rows,
        page=page,
        table_name=table_name,
        profile_id=profile_id,
    )
    # Reforço: se o adapter devolveu pouco, tenta BudgetParser genérico
    if len(parsed_items) < 3:
        parser = BudgetParser()
        fallback, _ = parser.parse_table(rows, page=page)
        row_scan = parser.parse_table_row_scan(rows, page=page)
        if len(row_scan) > len(fallback):
            fallback = row_scan
        if len(fallback) > len(parsed_items):
            parsed_items = fallback

    template_sem_precos = _rows_likely_missing_prices(rows)
    structured: list[dict[str, Any]] = []

    for idx, raw in enumerate(parsed_items, start=1):
        if not isinstance(raw, dict):
            continue
        descricao = str(raw.get("descricao") or "").strip()
        if len(descricao) < 3:
            continue
        item = _parser_row_to_structured(
            raw,
            page=page,
            table_id=table_id,
            index=idx,
            template_sem_precos=template_sem_precos,
        )
        item["_profile_id"] = profile_match.profile_id
        item["_table_kind"] = profile_match.table_kind
        structured.append(item)

    return structured


def _item_completeness_score(item: dict[str, Any]) -> int:
    score = 0
    if str(item.get("codigo") or "").strip():
        score += 4
    item_numero = str(item.get("item_numero") or item.get("item") or "").strip()
    if re.match(r"^\d+\.\d+\.\d+", item_numero):
        score += 3
    elif re.match(r"^\d+\.\d+", item_numero):
        score += 2
    if str(item.get("banco") or "").strip():
        score += 1
    return score


def _normalize_codigo(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "").strip().upper())


def _dedup_key(item: dict[str, Any]) -> str | None:
    """Chave de deduplicação: código+item, ou só item_numero (evita grupo duplicado)."""
    codigo = _normalize_codigo(item.get("codigo"))
    item_numero = _item_numero(item)
    if codigo:
        return f"{codigo}::{item_numero}" if item_numero else codigo
    if item_numero:
        return f"num::{item_numero}"
    return None


def _deduplicate_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    index_by_key: dict[str, int] = {}

    for raw in items:
        if not str(raw.get("codigo") or "").strip() and not str(raw.get("descricao") or "").strip():
            continue
        key = _dedup_key(raw)
        if key is None:
            result.append(raw)
            continue
        if key in index_by_key:
            idx = index_by_key[key]
            if _item_completeness_score(raw) > _item_completeness_score(result[idx]):
                result[idx] = raw
        else:
            index_by_key[key] = len(result)
            result.append(raw)

    return result


def _filter_for_analysis(
    items: list[dict[str, Any]],
    analysis_types: list[str],
) -> list[dict[str, Any]]:
    if "curva_abc" not in analysis_types:
        return [enrich_item_pricing_and_type(i) for i in items]
    return _select_items_for_curva_abc(items)


def _candidate_page(candidate: dict[str, Any]) -> int:
    return int(candidate.get("num_pagina") or candidate.get("pagina") or 1)


def _items_all_missing_prices(items: list[Any]) -> bool:
    executive: list[dict[str, Any]] = []
    for raw in items:
        if not isinstance(raw, dict):
            continue
        tipo = str(raw.get("tipo_linha") or raw.get("tipo") or "item").lower()
        desc = str(raw.get("descricao") or "").lower()
        if tipo == "grupo" or "total do grupo" in desc:
            continue
        executive.append(raw)
    if not executive:
        return False
    missing = sum(
        1
        for it in executive
        if _coerce_number(it.get("valor_unitario")) <= 0
        and _coerce_number(it.get("valor_total")) <= 0
    )
    return missing >= max(1, len(executive) // 2)


def _parser_has_reliable_structure(parser_items: list[dict[str, Any]]) -> bool:
    if len(parser_items) < 3:
        return False
    structured = 0
    for item in parser_items:
        if str(item.get("codigo") or "").strip():
            structured += 1
            continue
        item_numero = str(item.get("item_numero") or item.get("item") or "").strip()
        if re.match(r"^\d+(?:\.\d+)+$", item_numero):
            structured += 1
    return structured >= max(3, int(len(parser_items) * 0.4))


def _should_use_parser_as_primary(
    parser_items: list[dict[str, Any]],
    ai_items: list[dict[str, Any]],
    *,
    template_sem_precos: bool,
) -> bool:
    if not parser_items:
        return False
    if template_sem_precos or _items_all_missing_prices(ai_items):
        return True
    if _parser_has_reliable_structure(parser_items):
        return True
    return len(parser_items) >= max(3, int(len(ai_items) * 0.5))


def _resolve_rows(candidate: dict[str, Any]) -> list[list[Any]]:
    rows = candidate.get("rows")
    if not isinstance(rows, list) or not rows:
        raise HTTPException(
            status_code=409,
            detail="Cache de tabelas incompleto. Detecte as tabelas novamente.",
        )
    if count_nonempty_rows(rows) < 3:
        label = str(candidate.get("nome_tabela") or candidate.get("id") or "tabela")
        raise HTTPException(
            status_code=400,
            detail=f'A tabela "{label}" tem poucas linhas para análise.',
        )
    return rows


def _is_abc_ready_profile(profile_id: str, rows: list[list[Any]], table_name: str = "") -> bool:
    if profile_id == "curva_abc":
        return True
    parser = BudgetParser()
    name = (table_name or "").lower()
    if "curva abc" in name:
        return True
    for row in rows[:8]:
        if parser.is_abc_header_row(row):
            return True
    preview = _rows_preview_text(rows, limit=5)
    return "custo parcial" in preview and ("faixa" in preview or "incid" in preview)


def _diagnose_abc_row_parse(
    rows: list[list[Any]],
    *,
    page: int,
    table_id: str,
    profile_id: str | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Parse ABC/orcamento a partir das rows do cache com contadores de descarte."""
    parser = BudgetParser()
    raw_count = len(rows)
    header_idx = -1
    for idx, row in enumerate(rows[:25]):
        if parser.is_header_row(row) or parser.is_abc_header_row(row):
            header_idx = idx
            break

    candidates = 0
    rejected: dict[str, int] = {}
    data_rows = rows[header_idx + 1 :] if header_idx >= 0 else rows

    for row in data_rows:
        if not row or parser.should_ignore_row(row):
            rejected["ignored_or_empty"] = rejected.get("ignored_or_empty", 0) + 1
            continue
        if parser.is_header_row(row):
            rejected["header"] = rejected.get("header", 0) + 1
            continue
        candidates += 1

    items = _items_from_table_rows(
        rows,
        page=page,
        table_id=table_id,
        table_name="",
        profile_id=profile_id or "curva_abc",
    )
    accepted = len(items)
    rejected_silent = max(0, candidates - accepted)
    if rejected_silent:
        rejected["no_valor_or_short_desc"] = (
            rejected.get("no_valor_or_short_desc", 0) + rejected_silent
        )

    primary_reason = ""
    if rejected:
        primary_reason = max(rejected.items(), key=lambda kv: kv[1])[0]

    diagnostics = {
        "raw_rows": raw_count,
        "header_idx": header_idx,
        "candidate_rows": candidates,
        "accepted_items": accepted,
        "rejected": rejected,
        "primary_reject_reason": primary_reason,
        "profile_id": profile_id or "curva_abc",
        "source": "cached_table_rows",
    }
    logger.info(
        "[abc_rows] table=%s raw=%s candidates=%s accepted=%s rejected=%s reason=%s",
        table_id,
        raw_count,
        candidates,
        accepted,
        rejected,
        primary_reason,
    )
    return items, diagnostics


def _attach_abc_document_divergences(classified: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compara %/faixa do documento com o recálculo interno; não altera valores silenciosamente."""
    out: list[dict[str, Any]] = []
    for item in classified:
        row = dict(item)
        alerts = list(row.get("alertas") or [])
        doc_pct = row.get("doc_percentual")
        calc_pct = row.get("individual_percentage")
        if doc_pct is not None and calc_pct is not None:
            try:
                doc_f = float(doc_pct)
                # Documento pode vir 28.865 (%) ou 0.28865
                if doc_f <= 1.5:
                    doc_f *= 100.0
                calc_f = float(calc_pct)
                if abs(doc_f - calc_f) > 0.15:
                    msg = (
                        f"Divergência % incidência: documento={doc_f:.3f}% "
                        f"recalculado={calc_f:.3f}%"
                    )
                    if msg not in alerts:
                        alerts.append(msg)
            except (TypeError, ValueError):
                pass

        doc_faixa = str(row.get("doc_faixa") or "").strip().upper()[:1]
        calc_faixa = str(row.get("classification") or "").strip().upper()[:1]
        if doc_faixa in {"A", "B", "C"} and calc_faixa in {"A", "B", "C"} and doc_faixa != calc_faixa:
            msg = f"Divergência faixa ABC: documento={doc_faixa} recalculado={calc_faixa}"
            if msg not in alerts:
                alerts.append(msg)

        qtd = float(row.get("quantidade") or 0)
        vu = float(row.get("valor_unitario") or row.get("valor_unitario_com_bdi") or 0)
        vt = float(row.get("valor_total_com_bdi") or row.get("valor_total") or 0)
        if qtd > 0 and vu > 0 and vt > 0:
            esperado = qtd * vu
            if abs(vt - esperado) > max(0.02, esperado * 0.005):
                msg = (
                    f"Divergência qtd×VU: informado={vt:.2f} esperado={esperado:.2f}"
                )
                if msg not in alerts:
                    alerts.append(msg)

        row["alertas"] = alerts
        out.append(row)
    return out


def _build_finance_validation_from_items(
    items: list[dict[str, Any]],
    *,
    document_total: float | None = None,
) -> dict[str, Any]:
    from app.domain.abc_curve import is_executive_for_abc, line_total_com_bdi

    # Mesma base da Curva ABC / card do front (executivos elegíveis)
    base = [i for i in items if is_executive_for_abc(i)]
    if not base:
        base = [i for i in items if float(line_total_com_bdi(i) or 0) > 0]
    soma = sum(line_total_com_bdi(i) for i in base)
    total = float(document_total) if document_total and document_total > 0 else soma
    warnings: list[str] = []
    if document_total is None or document_total <= 0:
        warnings.append(
            f"Total Geral do documento não encontrado — usando soma_itens "
            f"({soma:,.2f})".replace(",", "X").replace(".", ",").replace("X", ".")
        )
    return {
        "ok": soma > 0,
        "total_geral": {
            "ok": True,
            "soma_folhas": round(soma, 2),
            "total_geral_documento": round(total, 2),
            "diferenca": 0.0,
            "itens_contados": len(base),
        },
        "subtotais": {"ok": True, "mismatches": []},
        "alertas": warnings,
    }


async def process_selected_tables(
    upload_id: str,
    user_id: str,
    table_ids: list[str],
    analysis_types: list[str],
) -> dict[str, Any]:
    """
    Extração via pipeline de 5 engines (coordenadas → validação → ABC).

    Para Curva ABC já pronta no PDF, prioriza as rows detectadas no cache
    (aliases de cabeçalho) em vez do parser por coordenadas hierárquicas.

    OpenAI NÃO lê o PDF. Insights opcionais só sobre JSON já validado.
    """
    from app.domain.budget_pipeline import PipelineRejectedError, run_pipeline
    from app.domain.budget_pipeline.insights_openai import generate_insights_from_structured_json
    from app.domain.abc_curve import classify_abc_items

    logger.info(
        "[process_selected] INÍCIO upload=%s tables=%s tipos=%s (pipeline_5_engines)",
        upload_id,
        table_ids,
        analysis_types,
    )

    upload_store = UploadStore()
    upload_id = UploadStore.validate_upload_id(upload_id)
    upload_store.assert_access(upload_id, user_id)

    meta = upload_store.load_meta(upload_id)
    filename = str(meta.get("filename") or f"{upload_id}.pdf")

    cache = TableCacheStore()
    options, _ = cache.get(upload_id)
    if not options:
        logger.warning("[process_selected] cache vazio upload=%s", upload_id)
        raise HTTPException(
            status_code=409,
            detail="Nenhuma tabela em cache. Volte e detecte as tabelas novamente.",
        )

    by_id = {str(o.get("id")): o for o in options if o.get("id")}
    unknown = [t for t in table_ids if t not in by_id]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Tabela(s) inválida(s): {', '.join(unknown)}",
        )

    # Classifica tabelas selecionadas e escolhe páginas do pipeline
    selected_kinds: dict[str, str] = {}
    selected_profiles: dict[str, str] = {}
    pages_0based: list[int] = []
    tables_out: list[dict[str, Any]] = []
    abc_ready_tables: list[str] = []
    extraction_diagnostics: list[dict[str, Any]] = []

    for tid in table_ids:
        cand = by_id[tid]
        cand_rows = _resolve_rows(cand)
        cand_name = str(cand.get("nome_tabela") or "")
        profile_match = match_profile(cand_rows, table_name=cand_name)
        selected_profiles[tid] = profile_match.profile_id
        kind = profile_match.table_kind or classify_table_kind(
            cand_rows, table_name=cand_name
        )
        selected_kinds[tid] = kind
        page = _candidate_page(cand)
        page_idx = max(0, page - 1)

        abc_ready = _is_abc_ready_profile(
            profile_match.profile_id, cand_rows, cand_name
        )
        if abc_ready:
            abc_ready_tables.append(tid)

        skip = kind in {"composicao", "analitico"} and not abc_ready
        tables_out.append(
            {
                "page": page,
                "table_id": tid,
                "rows": cand_rows,
                "original_rows": len(cand_rows),
                "columns": len(cand_rows[0]) if cand_rows else 0,
                "items_parsed": 0,
                "table_kind": kind,
                "profile_id": profile_match.profile_id,
                "skipped_for_abc": skip,
                "abc_ready": abc_ready,
            }
        )
        if not skip and page_idx not in pages_0based:
            pages_0based.append(page_idx)

    # --- Caminho preferencial: Curva ABC pronta nas rows do cache ---
    if abc_ready_tables:
        cached_items: list[dict[str, Any]] = []
        for tid in abc_ready_tables:
            cand = by_id[tid]
            cand_rows = _resolve_rows(cand)
            page = _candidate_page(cand)
            profile_id = selected_profiles.get(tid) or "curva_abc"
            if profile_id != "curva_abc" and not _is_abc_ready_profile(
                profile_id, cand_rows, str(cand.get("nome_tabela") or "")
            ):
                profile_id = "curva_abc"
            parsed, diag = _diagnose_abc_row_parse(
                cand_rows,
                page=page,
                table_id=tid,
                profile_id="curva_abc",
            )
            extraction_diagnostics.append(diag)
            cached_items.extend(parsed)
            for t in tables_out:
                if t["table_id"] == tid:
                    t["items_parsed"] = len(parsed)

        if cached_items:
            classified = classify_abc_items(cached_items)
            classified = _attach_abc_document_divergences(classified)
            abc_summary = build_abc_summary(classified)
            finance_validation = _build_finance_validation_from_items(classified)
            document_total = float(
                finance_validation["total_geral"]["total_geral_documento"] or 0
            )

            insights: dict[str, Any] = {"ok": False, "texto": "", "skipped": True}
            if is_openai_configured():
                insights = await generate_insights_from_structured_json(
                    {
                        "abc_summary": abc_summary,
                        "document_total": document_total,
                        "items": classified,
                        "validation": finance_validation,
                    }
                )
                insights["skipped"] = False

            valor_total = abc_summary.get("total_value") or document_total
            quarantine_count = abc_summary.get("quarantine_count") or 0
            eligible = [i for i in classified if i.get("classification")]
            message = (
                f"{len(eligible)} item(ns) elegíveis à Curva ABC "
                f"(tabela ABC detectada · rows do cache)"
            )
            if quarantine_count:
                message += f" — {quarantine_count} em quarentena"
            if insights.get("ok"):
                message += " · insights OpenAI gerados"

            pages_processed_1based = sorted(
                {
                    int(t["page"])
                    for t in tables_out
                    if t.get("abc_ready") and not t.get("skipped_for_abc")
                }
            )

            return {
                "status": "success",
                "upload_id": upload_id,
                "filename": filename,
                "tables_found": len(tables_out),
                "items_found": len(classified),
                "analysis_types": analysis_types,
                "engine": "cached_abc_rows",
                "pages_processed": pages_processed_1based,
                "document_kind": "curva_abc",
                "estimativo_meta": {},
                "tables": [
                    {k: v for k, v in t.items() if k != "rows"} for t in tables_out
                ],
                "items": classified,
                "structured_items": classified,
                "hierarchical_items": classified,
                "resumo": {
                    "total_items": abc_summary.get("total_items") or len(eligible),
                    "valor_total": valor_total,
                    "metodo": "cached_abc_rows",
                    "analysis_types": analysis_types,
                    "abc": abc_summary,
                    "quarantine_count": quarantine_count,
                    "validacao_financeira": finance_validation,
                    "insights": insights.get("texto") or "",
                    "document_kind": "curva_abc",
                    "estimativo_meta": {},
                },
                "abc_summary": abc_summary,
                "validacao_financeira": finance_validation,
                "extraction_diagnostics": extraction_diagnostics,
                "ia_metadata": {
                    "tables_processed": len(abc_ready_tables),
                    "pages_processed": pages_processed_1based,
                    "document_kind": "curva_abc",
                    "estimativo_meta": {},
                    "details": extraction_diagnostics,
                    "model": insights.get("model"),
                    "engine_used": "cached_abc_rows",
                    "abc_algorithm": "pareto_after_item_80_95",
                    "extraction_pipeline": "detect→header_aliases→abc",
                    "openai_role": "insights_only",
                    "insights_ok": bool(insights.get("ok")),
                },
                "message": f"{message} — {', '.join(analysis_types)}.",
            }

        logger.warning(
            "[process_selected] ABC detectada mas 0 itens nas rows — fallback pipeline. diag=%s",
            extraction_diagnostics,
        )

    has_sintetico = any(k == "sintetico" for k in selected_kinds.values())
    if has_sintetico:
        # Só páginas sintéticas no pipeline ABC
        pages_0based = []
        for tid in table_ids:
            if selected_kinds.get(tid) != "sintetico":
                continue
            page_idx = max(0, _candidate_page(by_id[tid]) - 1)
            if page_idx not in pages_0based:
                pages_0based.append(page_idx)
        logger.info(
            "[process_selected] sintético selecionado — páginas pipeline=%s",
            [p + 1 for p in pages_0based],
        )

    if not pages_0based:
        # Fallback: todas as páginas das tabelas não-composição
        for tid in table_ids:
            if selected_kinds.get(tid) in {"composicao", "analitico"} and tid not in abc_ready_tables:
                continue
            page_idx = max(0, _candidate_page(by_id[tid]) - 1)
            if page_idx not in pages_0based:
                pages_0based.append(page_idx)

    if not pages_0based:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nenhuma página orçamentária elegível nas tabelas selecionadas. "
                "Selecione o Orçamento Sintético / planilha de serviços."
            ),
        )

    pdf_path = upload_store.ensure_pdf(upload_id, user_id=user_id)

    # Expande páginas selecionadas via descoberta automática (planilha multi-página)
    from app.domain.budget_pipeline.page_discovery import discover_budget_pages

    hinted = sorted(pages_0based)
    # Páginas ABC selecionadas pelo usuário não devem ser removidas pela descoberta
    discovered = discover_budget_pages(
        pdf_path,
        hint_pages=hinted,
        allow_abc_hints=bool(abc_ready_tables),
    )
    pages_for_pipeline = discovered or hinted
    if abc_ready_tables:
        # Garante que as páginas selecionadas entrem no pipeline
        for p in hinted:
            if p not in pages_for_pipeline:
                pages_for_pipeline.append(p)
        pages_for_pipeline = sorted(pages_for_pipeline)
    logger.info(
        "[process_selected] páginas hint=%s descobertas=%s → pipeline=%s",
        [p + 1 for p in hinted],
        [p + 1 for p in discovered],
        [p + 1 for p in pages_for_pipeline],
    )

    pipeline = None
    pipeline_error: str | None = None
    try:
        pipeline = run_pipeline(
            pdf_path,
            pages=pages_for_pipeline,
            reject_on_validation_failure=True,
            run_abc="curva_abc" in analysis_types,
            auto_discover_pages=True,
        )
    except PipelineRejectedError as exc:
        pipeline_error = str(exc)
        logger.error("[process_selected] pipeline rejeitado: %s", pipeline_error)
        # Último recurso: tentar rows do cache mesmo sem perfil ABC
        fallback_items: list[dict[str, Any]] = []
        for tid in table_ids:
            if selected_kinds.get(tid) in {"composicao", "analitico"}:
                continue
            cand = by_id[tid]
            cand_rows = _resolve_rows(cand)
            page = _candidate_page(cand)
            parsed, diag = _diagnose_abc_row_parse(
                cand_rows,
                page=page,
                table_id=tid,
                profile_id=selected_profiles.get(tid),
            )
            extraction_diagnostics.append(diag)
            fallback_items.extend(parsed)

        if fallback_items:
            classified = classify_abc_items(fallback_items)
            classified = _attach_abc_document_divergences(classified)
            abc_summary = build_abc_summary(classified)
            finance_validation = _build_finance_validation_from_items(classified)
            for t in tables_out:
                if not t.get("skipped_for_abc"):
                    t["items_parsed"] = len(
                        [
                            i
                            for i in classified
                            if int(i.get("pagina") or i.get("_source_page") or 0)
                            == int(t["page"])
                        ]
                    )
            return {
                "status": "success",
                "upload_id": upload_id,
                "filename": filename,
                "tables_found": len(tables_out),
                "items_found": len(classified),
                "analysis_types": analysis_types,
                "engine": "cached_rows_fallback",
                "pages_processed": sorted({int(t["page"]) for t in tables_out}),
                "document_kind": "orcamento",
                "estimativo_meta": {},
                "tables": [
                    {k: v for k, v in t.items() if k != "rows"} for t in tables_out
                ],
                "items": classified,
                "structured_items": classified,
                "hierarchical_items": classified,
                "resumo": {
                    "total_items": abc_summary.get("total_items") or len(classified),
                    "valor_total": abc_summary.get("total_value") or 0,
                    "metodo": "cached_rows_fallback",
                    "analysis_types": analysis_types,
                    "abc": abc_summary,
                    "quarantine_count": abc_summary.get("quarantine_count") or 0,
                    "validacao_financeira": finance_validation,
                    "insights": "",
                    "document_kind": "orcamento",
                    "estimativo_meta": {},
                },
                "abc_summary": abc_summary,
                "validacao_financeira": finance_validation,
                "extraction_diagnostics": extraction_diagnostics,
                "ia_metadata": {
                    "tables_processed": len(tables_out),
                    "pages_processed": sorted({int(t["page"]) for t in tables_out}),
                    "document_kind": "orcamento",
                    "details": extraction_diagnostics,
                    "engine_used": "cached_rows_fallback",
                    "pipeline_error": pipeline_error,
                    "abc_algorithm": "pareto_after_item_80_95",
                    "extraction_pipeline": "detect→header_aliases→fallback",
                    "openai_role": "insights_only",
                },
                "message": (
                    f"{len(classified)} item(ns) via fallback de rows "
                    f"(pipeline: {pipeline_error}) — {', '.join(analysis_types)}."
                ),
            }

        # Monta diagnóstico útil para a UI
        primary = ""
        if extraction_diagnostics:
            primary = str(
                extraction_diagnostics[0].get("primary_reject_reason") or ""
            )
        raise HTTPException(
            status_code=400,
            detail={
                "message": pipeline_error,
                "user_message": (
                    "Tabela encontrada, mas nenhuma linha pôde ser convertida em item."
                    + (f" Motivo principal: {primary}." if primary else "")
                ),
                "validacao_financeira": (
                    exc.result.validation.to_dict() if exc.result else {}
                ),
                "extraction_diagnostics": extraction_diagnostics,
                "engine": "budget_pipeline_5_engines",
            },
        ) from exc

    classified = pipeline.items
    abc_summary = pipeline.abc_summary or build_abc_summary(classified)
    hierarchical = pipeline.hierarchy or classified
    finance_validation = {
        "ok": pipeline.validation.ok,
        "total_geral": {
            "ok": pipeline.validation.ok,
            "soma_folhas": pipeline.validation.soma_folhas,
            "total_geral_documento": pipeline.validation.total_geral_documento,
            "diferenca": pipeline.validation.diferenca_total,
        },
        "subtotais": {
            "ok": len(pipeline.validation.subtotal_mismatches) == 0,
            "mismatches": pipeline.validation.subtotal_mismatches,
        },
        "alertas": pipeline.validation.errors + pipeline.validation.warnings,
    }

    for t in tables_out:
        if not t.get("skipped_for_abc"):
            t["items_parsed"] = len(
                [
                    i
                    for i in classified
                    if int(i.get("pagina") or i.get("_source_page") or 0) == int(t["page"])
                ]
            )

    # Insights OpenAI (opcional) — só JSON, nunca PDF
    insights = {"ok": False, "texto": "", "skipped": True}
    if is_openai_configured():
        insights = await generate_insights_from_structured_json(
            {
                "abc_summary": abc_summary,
                "document_total": pipeline.document_total,
                "items": classified,
                "validation": finance_validation,
            }
        )
        insights["skipped"] = False

    valor_total = abc_summary.get("total_value") or 0
    quarantine_count = abc_summary.get("quarantine_count") or 0
    eligible = [i for i in classified if i.get("classification")]
    message = (
        f"{len(eligible)} item(ns) elegíveis à Curva ABC "
        f"(pipeline 5 engines · validação R$ 0,01)"
    )
    if quarantine_count:
        message += f" — {quarantine_count} em quarentena"
    if insights.get("ok"):
        message += " · insights OpenAI gerados"

    resumo = {
        "total_items": abc_summary.get("total_items") or len(eligible),
        "valor_total": valor_total,
        "metodo": "budget_pipeline_5_engines",
        "analysis_types": analysis_types,
        "abc": abc_summary,
        "quarantine_count": quarantine_count,
        "validacao_financeira": finance_validation,
        "insights": insights.get("texto") or "",
        "document_kind": pipeline.document_kind,
        "estimativo_meta": pipeline.estimativo_meta or {},
    }

    if pipeline.estimativo_meta.get("valor_anual_adotado"):
        message += (
            f" · estimativo SRP R$ {float(pipeline.estimativo_meta['valor_anual_adotado']):,.2f}"
            .replace(",", "X")
            .replace(".", ",")
            .replace("X", ".")
        )

    pages_processed_1based = [
        int(p) for p in (pipeline.pages_processed or []) if int(p) >= 1
    ]

    return {
        "status": "success",
        "upload_id": upload_id,
        "filename": filename,
        "tables_found": len(tables_out),
        "items_found": len(classified),
        "analysis_types": analysis_types,
        "engine": "budget_pipeline_5_engines",
        "pages_processed": pages_processed_1based,
        "document_kind": pipeline.document_kind,
        "estimativo_meta": pipeline.estimativo_meta or {},
        "tables": [{k: v for k, v in t.items() if k != "rows"} for t in tables_out],
        "items": classified,
        "structured_items": hierarchical if isinstance(hierarchical, list) else classified,
        "hierarchical_items": hierarchical if isinstance(hierarchical, list) else classified,
        "resumo": resumo,
        "abc_summary": abc_summary,
        "validacao_financeira": finance_validation,
        "extraction_diagnostics": extraction_diagnostics,
        "ia_metadata": {
            "tables_processed": len([t for t in tables_out if not t.get("skipped_for_abc")]),
            "pages_processed": pages_processed_1based,
            "document_kind": pipeline.document_kind,
            "estimativo_meta": pipeline.estimativo_meta or {},
            "details": pipeline.engine_logs + extraction_diagnostics,
            "model": insights.get("model"),
            "engine_used": "budget_pipeline_5_engines",
            "abc_algorithm": "pareto_after_item_80_95",
            "extraction_pipeline": "layout→table→validator→normalizer→analytics",
            "openai_role": "insights_only",
            "insights_ok": bool(insights.get("ok")),
        },
        "message": f"{message} — {', '.join(analysis_types)}.",
    }
