"""
Insights via OpenAI — SOMENTE sobre JSON já estruturado/validado.

Nunca envia PDF, nunca reconstrói tabelas, nunca calcula subtotais/ABC.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config import OPENAI_ORCAMENTO_MODEL, get_openai_api_key, is_openai_configured

logger = logging.getLogger(__name__)

_INSIGHTS_SYSTEM = """Você é um engenheiro de custos especializado em orçamentos de obras públicas brasileiras.
Você recebe um JSON JÁ VALIDADO (itens + Curva ABC). Regras obrigatórias:
- NÃO invente itens, códigos, quantidades ou valores.
- NÃO recalcule a Curva ABC (já está no JSON).
- NÃO peça o PDF.
- Use apenas os dados fornecidos.
Responda em português, de forma objetiva, com:
1) Resumo executivo (3–5 linhas)
2) Principais itens classe A e risco de custo
3) Observações / inconsistências aparentes nos dados (se houver)
4) Sugestões práticas de negociação ou revisão
"""


async def generate_insights_from_structured_json(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """
    Chama OpenAI apenas com o JSON estruturado.
    Retorna {ok, texto, model, error?}.
    """
    if not is_openai_configured():
        return {
            "ok": False,
            "texto": "",
            "model": None,
            "error": "OPENAI_API_KEY não configurada",
        }

    # Payload enxuto — sem linhas incompletas enormes
    slim = {
        "abc_summary": payload.get("abc_summary") or {},
        "total_geral": payload.get("document_total") or payload.get("valor_total"),
        "total_items": (payload.get("abc_summary") or {}).get("total_items"),
        "top_items": [
            {
                "item": i.get("item_numero") or i.get("item"),
                "codigo": i.get("codigo"),
                "descricao": (i.get("descricao") or "")[:120],
                "valor_total": i.get("valor_total_com_bdi") or i.get("valor_total"),
                "classification": i.get("classification"),
                "accumulated_percentage": i.get("accumulated_percentage"),
            }
            for i in (payload.get("items") or [])
            if i.get("classification") in {"A", "B"}
        ][:25],
        "validation": payload.get("validation") or payload.get("validacao_financeira"),
    }

    try:
        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=get_openai_api_key(), timeout=60.0)
        response = await client.chat.completions.create(
            model=OPENAI_ORCAMENTO_MODEL,
            temperature=0.2,
            messages=[
                {"role": "system", "content": _INSIGHTS_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        "Analise o seguinte JSON de orçamento (já validado matematicamente):\n\n"
                        + json.dumps(slim, ensure_ascii=False, default=str)
                    ),
                },
            ],
        )
        texto = (response.choices[0].message.content or "").strip()
        logger.info("[insights] OpenAI ok model=%s chars=%s", OPENAI_ORCAMENTO_MODEL, len(texto))
        return {"ok": True, "texto": texto, "model": OPENAI_ORCAMENTO_MODEL}
    except Exception as exc:
        logger.warning("[insights] falhou: %s", exc)
        return {"ok": False, "texto": "", "model": OPENAI_ORCAMENTO_MODEL, "error": str(exc)}
