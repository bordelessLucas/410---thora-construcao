"""
Extração de itens orçamentários a partir de tabelas selecionadas.
Usa OpenAI (híbrido com parser local) para análise precisa; fallback para BudgetParser.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from budget_parser import BudgetParser
from config import OPENAI_ORCAMENTO_MODEL, is_openai_configured
from fastapi import HTTPException
from services.hybrid_extraction import merge_parser_as_primary
from services.openai_service import OpenAIServiceError, process_selected_table

from app.domain.abc_curve import (
    build_abc_summary,
    classify_abc_items,
    enrich_item_pricing_and_type,
    infer_tipo_linha,
)
from app.domain.money import parse_brl, sanitize_bdi_percent
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
) -> list[dict[str, Any]]:
    parser = BudgetParser()
    parsed_items, _ = parser.parse_table(rows, page=page)
    row_scan_items = parser.parse_table_row_scan(rows, page=page)
    if len(row_scan_items) > len(parsed_items):
        parsed_items = row_scan_items
    template_sem_precos = _rows_likely_missing_prices(rows)
    structured: list[dict[str, Any]] = []

    for idx, raw in enumerate(parsed_items, start=1):
        if not isinstance(raw, dict):
            continue
        descricao = str(raw.get("descricao") or "").strip()
        if len(descricao) < 3:
            continue
        structured.append(
            _parser_row_to_structured(
                raw,
                page=page,
                table_id=table_id,
                index=idx,
                template_sem_precos=template_sem_precos,
            )
        )

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
    """Chave de deduplicação: só aplica quando há código; item_numero distingue linhas do edital."""
    codigo = _normalize_codigo(item.get("codigo"))
    if not codigo:
        return None
    item_numero = str(item.get("item_numero") or item.get("item") or "").strip()
    return f"{codigo}::{item_numero}" if item_numero else codigo


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
        return items

    filtered: list[dict[str, Any]] = []
    for item in items:
        enriched = enrich_item_pricing_and_type(item)
        tipo = str(enriched.get("tipo_linha") or enriched.get("tipo") or "item").lower()
        descricao = str(enriched.get("descricao") or "").lower()
        if tipo == "grupo" or "total do grupo" in descricao:
            continue
        # Composições sem numeração executiva X.Y.Z ficam de fora da ABC
        item_numero = str(enriched.get("item_numero") or enriched.get("item") or "").strip()
        if tipo == "composicao" and not re.match(r"^\d+\.\d+\.\d+", item_numero):
            continue
        q = _coerce_number(enriched.get("quantidade"))
        vu = _coerce_number(enriched.get("valor_unitario"))
        vt = _coerce_number(enriched.get("valor_total_com_bdi") or enriched.get("valor_total"))
        if q <= 0 and vu <= 0 and vt <= 0:
            continue
        filtered.append(enriched)
    return filtered


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


async def process_selected_tables(
    upload_id: str,
    user_id: str,
    table_ids: list[str],
    analysis_types: list[str],
) -> dict[str, Any]:
    if not is_openai_configured():
        raise HTTPException(
            status_code=503,
            detail=(
                "A chave da OpenAI não está configurada. "
                "Defina OPENAI_API_KEY no ambiente para análise com IA."
            ),
        )

    logger.info(
        "[process_selected] INÍCIO upload=%s tables=%s tipos=%s",
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
    logger.info("[process_selected] cache com %s opção(ões)", len(options))

    by_id = {str(o.get("id")): o for o in options if o.get("id")}
    unknown = [t for t in table_ids if t not in by_id]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Tabela(s) inválida(s): {', '.join(unknown)}",
        )

    all_structured: list[dict[str, Any]] = []
    all_hierarchical: list[dict[str, Any]] = []
    tables_out: list[dict[str, Any]] = []
    ia_metadata_list: list[dict[str, Any]] = []
    pdf_bytes: bytes | None = None

    def _pdf_bytes_for_table(*, has_table_image: bool) -> bytes:
        nonlocal pdf_bytes
        if has_table_image:
            return b""
        if pdf_bytes is None:
            pdf_bytes = upload_store.ensure_pdf(upload_id, user_id=user_id).read_bytes()
        return pdf_bytes

    for table_id in table_ids:
        candidate = by_id[table_id]
        logger.info(
            "[process_selected] tabela %s pág=%s score=%s",
            table_id,
            candidate.get("pagina") or candidate.get("num_pagina"),
            candidate.get("budget_score"),
        )
        rows = _resolve_rows(candidate)
        page = _candidate_page(candidate)
        candidate_name = str(candidate.get("nome_tabela") or "")
        table_image_b64 = candidate.get("imagem_base64")
        template_sem_precos = _rows_likely_missing_prices(rows)
        parser_items = _items_from_table_rows(rows, page=page, table_id=table_id)

        tables_out.append(
            {
                "page": page,
                "table_id": table_id,
                "rows": rows,
                "original_rows": len(rows),
                "columns": len(rows[0]) if rows else 0,
                "items_parsed": 0,
            }
        )

        try:
            structured_data, provider_used = await process_selected_table(
                _pdf_bytes_for_table(has_table_image=bool(table_image_b64)),
                table_id,
                table_rows=rows,
                table_page=page,
                table_name=candidate_name or None,
                table_image_base64=table_image_b64,
                user_id=user_id,
            )
        except OpenAIServiceError as exc:
            logger.warning("Erro IA na tabela %s: %s", table_id, exc)
            raise HTTPException(
                status_code=getattr(exc, "status_code", 500) or 500,
                detail=f"Erro na extração IA da tabela {table_id}: {exc}",
            ) from exc

        items_this_table = structured_data.get("items") or []
        hierarchical_this_table = structured_data.get("hierarchical_items") or []

        if not items_this_table:
            logger.warning(
                "IA retornou 0 itens para %s (pág %s). Usando parser local.",
                table_id,
                page,
            )
            items_this_table = parser_items
            if items_this_table:
                ia_metadata_list.append(
                    {
                        "table_id": table_id,
                        "provider": "local:budget_parser_fallback",
                        "resumo": {"total_items": len(items_this_table)},
                    }
                )
        elif _should_use_parser_as_primary(
            parser_items,
            items_this_table,
            template_sem_precos=template_sem_precos,
        ):
            logger.info(
                "Parser como base estrutural (%s itens) + IA (%s itens) em %s",
                len(parser_items),
                len(items_this_table),
                table_id,
            )
            items_this_table = merge_parser_as_primary(parser_items, items_this_table)
            ia_metadata_list.append(
                {
                    "table_id": table_id,
                    "provider": "local:parser_primary_hibrido",
                    "resumo": {"total_items": len(items_this_table)},
                }
            )
        else:
            ia_metadata_list.append(
                {
                    "table_id": table_id,
                    "provider": provider_used,
                    "resumo": structured_data.get("resumo") or {},
                }
            )

        for raw_item in items_this_table:
            if isinstance(raw_item, dict):
                raw_item.setdefault("origem_extracao", "openai_hybrid")
                raw_item.setdefault("_source_table_id", table_id)
                raw_item.setdefault("_source_page", page)
        for raw_item in hierarchical_this_table:
            if isinstance(raw_item, dict):
                raw_item.setdefault("_source_table_id", table_id)
                raw_item.setdefault("_source_page", page)

        all_structured.extend(items_this_table)
        all_hierarchical.extend(items_this_table)
        tables_out[-1]["items_parsed"] = len(items_this_table)

        logger.info(
            "IA híbrida: tabela %s pág %s → %s itens (%s)",
            table_id,
            page,
            len(items_this_table),
            provider_used,
        )

    deduped = _deduplicate_items(all_structured)
    filtered = _filter_for_analysis(deduped, analysis_types)

    if not filtered and deduped:
        filtered = [
            enrich_item_pricing_and_type(i)
            for i in deduped
            if str(i.get("tipo_linha", i.get("tipo"))) != "grupo"
        ]

    if not filtered:
        raise HTTPException(
            status_code=400,
            detail=(
                "Nenhum item executivo encontrado nas tabelas selecionadas. "
                "Verifique se a planilha contém Código, Qtde e Preço."
            ),
        )

    # Contrato + tipagem + Curva ABC canônica (fonte de verdade)
    classified = classify_abc_items(filtered)
    abc_summary = build_abc_summary(classified)
    hierarchical_deduped = [enrich_item_pricing_and_type(i) for i in deduped]

    valor_total = abc_summary["total_value"]
    quarantine_count = abc_summary["quarantine_count"]
    resumo = {
        "total_items": abc_summary["total_items"],
        "valor_total": valor_total,
        "metodo": OPENAI_ORCAMENTO_MODEL,
        "analysis_types": analysis_types,
        "abc": abc_summary,
        "quarantine_count": quarantine_count,
    }

    eligible = [i for i in classified if i.get("abc_elegivel")]
    message = (
        f"{len(eligible)} item(ns) elegíveis à Curva ABC "
        f"({OPENAI_ORCAMENTO_MODEL})"
    )
    if quarantine_count:
        message += f" — {quarantine_count} em quarentena (revisar)"

    return {
        "status": "success",
        "upload_id": upload_id,
        "filename": filename,
        "tables_found": len(tables_out),
        "items_found": len(classified),
        "analysis_types": analysis_types,
        "engine": "openai_hybrid",
        "tables": tables_out,
        "items": classified,
        "structured_items": hierarchical_deduped,
        "hierarchical_items": hierarchical_deduped,
        "resumo": resumo,
        "abc_summary": abc_summary,
        "ia_metadata": {
            "tables_processed": len(ia_metadata_list),
            "details": ia_metadata_list,
            "model": OPENAI_ORCAMENTO_MODEL,
            "engine_used": "openai_hybrid",
            "abc_algorithm": "pareto_before_item_80_95",
        },
        "message": f"{message} — {', '.join(analysis_types)}.",
    }
