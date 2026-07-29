"""
Integração base com OpenAI para o fluxo de Orçamento Analítico.
Chave via OPENAI_API_KEY (nunca embutida no código).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx
import pdfplumber
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI, BadRequestError, OpenAIError, RateLimitError

from config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
    OPENAI_API_KEY,
    OPENAI_MODEL,
    OPENAI_ORCAMENTO_MODEL,
    OPENAI_ORCAMENTO_TIMEOUT_SECONDS,
    ai_keys_status,
    get_gemini_api_key,
    get_openai_api_key,
    is_gemini_configured,
    is_openai_configured,
    resolve_ai_extraction_provider,
)

from .ai_audit_logger import log_ai_exchange, truncate_rows_for_audit
from .analitico_normalize import normalize_hierarchical_analitico
from .budget_scoring import (
    BudgetPageCandidate,
    detect_budget_pages,
    detect_readable_pages_for_rescan,
)
from .hybrid_extraction import (
    detect_table_structure,
    enrich_hierarchical_items,
    merge_ai_with_parser,
)

logger = logging.getLogger(__name__)

_MAX_DETECTION_PAGES = 4
_MAX_DETECTION_CHARS_PER_PAGE = 3200
_MAX_TABLE_ROWS_FOR_PROMPT = 90
_MAX_CELL_CHARS = 120

DETECTION_SYSTEM_PROMPT = (
    "Você é um especialista em engenharia de custos. Sua tarefa é localizar tabelas relevantes "
    "de orçamento de obras em PDFs. Considere apenas as páginas iniciais fornecidas. "
    "Retorne a resposta obrigatoriamente no formato JSON."
)

DETECTION_USER_PROMPT_TEMPLATE = {
    "objetivo": "Identificar tabelas de orçamento com alta precisão",
    "tipos_desejados": ["Orçamento", "Planilha de Quantitativos", "Composições"],
    "regras": [
        "Retorne somente tabelas com forte evidência de serem relevantes para orçamento de obras.",
        "Ignore páginas de capa, sumário, páginas institucionais, logos, cabeçalhos e rodapés.",
        "Se uma página tiver apenas texto corrido sem tabela, não invente tabela.",
        "Use preview_texto com uma amostra literal curta do conteúdo encontrado.",
        "Ordene as tabelas por relevância descrescente.",
    ],
    "schema_saida": {
        "tables": [
            {
                "id": "table-1",
                "nome_tabela": "Orçamento Estimado",
                "num_pagina": 1,
                "preview_texto": "Item | Descrição | Unidade | Quantidade | Valor Unitário | Total",
            }
        ]
    },
}

EXTRACTION_SYSTEM_PROMPT = (
    "Você é um Engenheiro de Custos especialista em análise de dados. Seu único objetivo é extrair INSumos, SERVIÇOS, MATERIAIS e EQUIPAMENTOS de planilhas orçamentárias. SE o texto fornecido for apenas descritivo, cláusulas de edital, regras de contrato ou descrição de objeto, RETORNE UM ARRAY VAZIO. Nunca transforme cláusulas em itens de custo.\n\n"
    "Sua única missão é extrair os itens da planilha de ORÇAMENTO DETALHADO de arquivos PDF de licitações e estruturá-los em um JSON estrito.\n\n"
    "REGRAS DE EXTRAÇÃO E MAPEAMENTO ESPACIAL (MUITO IMPORTANTE):\n\n"
    "0. ORDEM DAS COLUNAS NO PDF (da esquerda para a direita): Código | Descrição do Serviço | BDI | Unid. | Qtde | Preço Unit. | Preço total. "
    "Variante comum em licitações: Código | Descrição | BDI | Unid. | Qtde. Máxima | Qtde. Mínima | Preço Unit. | Preço total — use Qtde. Máxima como 'quantidade' quando existirem duas colunas de qtde. "
    "O campo JSON 'codigo' vem da coluna Código (ex: CPU6724, 5914351M). O campo 'item' é a numeração hierárquica (ex: 1.1, 2.3) se existir em coluna separada; não repita o código no campo item.\n"
    "0b. PREÇOS NA IMAGEM: Leia Preço Unit. e Preço total diretamente da imagem quando o texto extraído vier vazio ou zerado. Nunca deixe valor_unitario e valor_total zerados se a imagem mostrar valores.\n"
    "1. IDENTIFICAÇÃO DE COLUNAS POR CABEÇALHO: Localize cada coluna pelo cabeçalho exato antes de extrair valores.\n"
    "2. EXTRAÇÃO BASEADA EM LINHA: Cada valor deve pertencer à coluna vertical correta. Célula vazia → 0.0. NUNCA desloque Qtde para Preço Unit. ou Preço total.\n"
    "3. RIGOR COM NÚMEROS E UNIDADES: 'unidade' só com siglas (M2, M3, UN, m, T, TKm). Separe número e unidade se vierem juntos.\n"
    "4. LIMPEZA MATEMÁTICA: Remova 'R$' e '%'. Converta padrão brasileiro para float (ex: '3.017,500' → 3017.5; '17.260,10' → 17260.10; '20,81' → 20.81).\n"
    "5. COLUNA BDI (%): Extraia o percentual da coluna BDI de cada linha de serviço (ex: '20,81' ou '20,81%' → 20.81). Não use taxas de páginas de resumo de BDI.\n"
    "6. PREÇO COM BDI: Quando existirem colunas 'Preço Unit. S/ BDI' e 'Preço Unit. C/ BDI' (ou 'Preço Total S/ BDI' e 'Preço Total C/ BDI'), "
    "use SEMPRE os valores **C/ BDI** (com BDI) em valor_unitario e valor_total. Esses são os valores econômicos corretos para Curva ABC.\n"
    "7. FÓRMULA DE CONSISTÊNCIA: quantidade × valor_unitario ≈ valor_total (tolerância ~2%). Se não bater, re-leia a linha e corrija o alinhamento das colunas.\n"
    "8. HIERARQUIA (tipo_linha): Preserve a ordem sequencial do PDF.\n"
    "   - 'grupo': títulos de capítulo/subgrupo (ex: '1. GRUPO 1: SERVIÇOS DEMOLIÇÃO') ou linhas 'Total do grupo'.\n"
    "   - 'item': serviços principais com código e quantidade (ex: numeração 1.1, 1.2). SOMENTE estes entram na Curva ABC.\n"
    "   - 'composicao': insumos/subitens indentados sob um item (composição analítica, ex: 1.1.1). "
    "Extraia para planilha analítica, mas NÃO repita o valor total do item pai — use apenas o custo do insumo.\n"
    "9. item_numero: numeração hierárquica exata do edital (ex: '1', '1.1', '1.1.1'). Use null se ausente.\n"
    "   A coluna item_numero DEVE preservar rigorosamente a numeração original do PDF (ex: 1, 1.1, 1.1.1). Não crie sequências novas, copie o índice exato que está no documento.\n"
    "10. banco: fonte de referência (SINAPI, SICRO, Próprio, etc.). Use null se não houver.\n"
    "11. REMOÇÃO DE LIXO: Ignore cabeçalhos repetidos de coluna, 'RESUMO GERAL', 'MAPA DE COTAÇÃO', linhas institucionais, "
    "linhas de percentual (% do total) sem quantidade/preço, e subtotais de grupo.\n"
    "    NÃO remova linhas de grupo, item ou composição da planilha analítica.\n\n"
    "FORMATO DE SAÍDA OBRIGATÓRIO (JSON):\n"
    "Retorne um array de objetos JSON chamado orcamento_itens seguindo exatamente este schema:\n"
    "{\n"
    '  "orcamento_itens": [\n'
    "    {\n"
    '      "item_numero": "string|null",\n'
    '      "tipo_linha": "grupo|item|composicao",\n'
    '      "banco": "string|null",\n'
    '      "codigo": "string",\n'
    '      "descricao": "string",\n'
    '      "bdi": 0.0,\n'
    '      "unidade": "string",\n'
    '      "quantidade": 0.0,\n'
    '      "valor_unitario": 0.0,\n'
    '      "valor_total": 0.0\n'
    "    }\n"
    "  ]\n"
    "}"
)

EXTRACTION_USER_PROMPT_HEADER = (
    "Extraia a tabela selecionada seguindo rigorosamente as regras do sistema, em especial: "
    "MAPEAMENTO ESPACIAL, valores C/ BDI quando existirem, SANITY CHECK (Qtd × VU ≈ Total), "
    "e tipo_linha correto (item = serviço executivo para Curva ABC; composicao = insumo sem duplicar valor do pai)."
)

ANALITICO_FULL_SYSTEM_PROMPT = (
    "Você é especialista em orçamentos de obras públicas (NOVACAP / licitações). "
    "Extraia dados de QUALQUER formato presente na página:\n"
    "• planilhas e tabelas;\n"
    "• texto corrido, cláusulas, memorial descritivo, listas numeradas;\n"
    "• imagens, quadros escaneados, carimbos e anexos visuais.\n\n"
    "REGRA PRINCIPAL: NÃO exija tabela. Se houver serviço/obrigação com quantidade, unidade e/ou "
    "valor (R$, preço unitário, total, qtde), crie uma linha em orcamento_itens.\n\n"
    "tipo_linha: 'grupo' (títulos de capítulo), 'item' (serviços), 'composicao' (insumos/subitens).\n"
    "codigo: código do serviço quando existir; use \"\" (string vazia) se não houver.\n"
    "descricao: obrigatória para itens/composições (resumo do serviço).\n"
    "banco: SINAPI, SICRO, etc. ou null.\n"
    "bdi, quantidade, valor_unitario, valor_total: números em padrão brasileiro convertidos para float.\n"
    "Se só houver valor total, preencha valor_total e estime quantidade=1 se necessário.\n"
    "rotulo_linha, tipo_categoria, porcentagem: quando existirem; senão null ou 0.0.\n"
    "item_numero: numeração hierárquica (1, 1.1, 1.1.1) ou null.\n\n"
    "A coluna item_numero DEVE preservar rigorosamente a numeração original do PDF (ex: 1, 1.1, 1.1.1). Não invente ou reindexe os itens.\n\n"
    "Ignore apenas: cabeçalhos repetidos de coluna, páginas 100% jurídicas SEM números de obra. "
    "Se existir R$, qtde, m², m³, UN ou valores numéricos de custo, EXTRAIA.\n"
    "Preserve a ordem de leitura (topo→baixo).\n"
    "Retorne orcamento_itens vazio APENAS se a página não tiver qualquer dado numérico de custo/quantidade."
)

FULL_PDF_ANALITICO_SYSTEM_PROMPT = ANALITICO_FULL_SYSTEM_PROMPT

FULL_PDF_PAGE_USER_TEMPLATE = (
    "Extraia TODAS as linhas orçamentárias desta página ({page}/{total_pages}). "
    "Inclua grupos, itens, composições e insumos — em tabela OU em texto corrido.\n"
    "Para texto narrativo: cada serviço com quantidade/unidade/preço vira um item no JSON.\n\n"
    "TEXTO EXTRAÍDO DA PÁGINA (use junto com a imagem):\n{text_snippet}"
)

FULL_PDF_TEXTUAL_RETRY_ADDON = (
    "\n\nREFORÇO — MODO TEXTO CORRIDO:\n"
    "Esta página parece conter orçamento em prosa ou edital com valores (R$, qtde, unidades). "
    "Extraia TODOS os serviços/itens mencionados com valores e quantidades, mesmo sem tabela visível. "
    "Não retorne array vazio se existirem valores monetários ou quantidades de serviços no texto/imagem."
)

FULL_PDF_AGGRESSIVE_RETRY_ADDON = (
    "\n\nVARREDURA AGRESSIVA:\n"
    "A página tem números/valores visíveis. Extraia TUDO: cada menção a serviço, quantidade, "
    "unidade (m², m³, un, h), preço unitário, total, BDI, mesmo em cláusulas do edital. "
    "Crie linhas tipo_linha='item' para cada bloco com custo. NÃO retorne array vazio."
)

_MAX_FULL_PDF_PAGES = int(os.getenv("ANALITICO_MAX_PAGES", "60"))
_ANALITICO_PAGE_DELAY_SECONDS = float(os.getenv("ANALITICO_PAGE_DELAY", "0.45"))
ProgressCallback = Callable[[Dict[str, Any]], None]


EXTRACTION_JSON_SCHEMA = {
    "name": "orcamento_schema",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "orcamento_itens": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "item_numero": {"type": ["string", "null"]},
                        "tipo_linha": {
                            "type": "string",
                            "enum": ["grupo", "item", "composicao"],
                        },
                        "banco": {"type": ["string", "null"]},
                        "codigo": {"type": "string"},
                        "descricao": {"type": "string"},
                        "bdi": {"type": "number"},
                        "unidade": {"type": "string"},
                        "quantidade": {"type": "number"},
                        "valor_unitario": {"type": "number"},
                        "valor_total": {"type": "number"},
                        "rotulo_linha": {"type": ["string", "null"]},
                        "tipo_categoria": {"type": ["string", "null"]},
                        "porcentagem": {"type": "number"},
                    },
                    "required": [
                        "item_numero",
                        "tipo_linha",
                        "banco",
                        "codigo",
                        "descricao",
                        "bdi",
                        "unidade",
                        "quantidade",
                        "valor_unitario",
                        "valor_total",
                        "rotulo_linha",
                        "tipo_categoria",
                        "porcentagem",
                    ],
                    "additionalProperties": False
                }
            }
        },
        "required": ["orcamento_itens"],
        "additionalProperties": False
    }
}

class OpenAIServiceError(Exception):
    def __init__(self, message: str, *, status_code: int = 500, code: str = "openai_error"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


_PLACEHOLDER_API_KEYS = frozenset(
    {
        "",
        "sua-chave-aqui",
        "sua-chave",
        "your-key-here",
        "changeme",
        "...",
    }
)

_GEMINI_CANDIDATE_MODELS = [
    GEMINI_MODEL,
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-1.5-flash-latest",
    "gemini-1.5-pro-latest",
]


def _is_valid_api_key(key: str | None) -> bool:
    normalized = (key or "").strip().lower()
    return bool(normalized) and normalized not in _PLACEHOLDER_API_KEYS


def _has_openai_key() -> bool:
    return is_openai_configured()


def _has_gemini_key() -> bool:
    return is_gemini_configured()


def _missing_ai_key_message() -> str:
    status = ai_keys_status()
    hint = status.get("hint") or "Configure OPENAI_API_KEY ou GEMINI_API_KEY."
    return f"Nenhuma chave de IA configurada no servidor. {hint}"


def _resolve_extraction_provider() -> str:
    provider = resolve_ai_extraction_provider()
    if provider:
        return provider
    raise OpenAIServiceError(
        _missing_ai_key_message(),
        status_code=503,
        code="missing_api_key",
    )


def _ensure_api_key() -> None:
    _resolve_extraction_provider()


async def _call_gemini_json(
    *,
    system_msg: str,
    user_text: str,
    base64_image: str | None = None,
    image_mime: str = "image/jpeg",
    timeout_seconds: float = OPENAI_ORCAMENTO_TIMEOUT_SECONDS,
) -> Tuple[str, str]:
    if not _has_gemini_key():
        raise OpenAIServiceError(
            "GEMINI_API_KEY ausente ou inválida.",
            status_code=503,
            code="missing_api_key",
        )

    parts: List[Dict[str, Any]] = [{"text": user_text}]
    if base64_image:
        parts.append(
            {
                "inline_data": {
                    "mime_type": image_mime,
                    "data": base64_image.strip(),
                }
            }
        )

    request_body = {
        "system_instruction": {"parts": [{"text": system_msg}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "temperature": 0,
            "maxOutputTokens": 8192,
            "responseMimeType": "application/json",
        },
    }

    attempted_models: List[str] = []
    last_error_body = ""

    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        for model in _GEMINI_CANDIDATE_MODELS:
            if not model or model in attempted_models:
                continue
            attempted_models.append(model)
            url = (
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                f"?key={get_gemini_api_key()}"
            )
            try:
                response = await client.post(url, json=request_body)
            except httpx.HTTPError as exc:
                raise OpenAIServiceError(
                    f"Falha de conexão com o Gemini: {exc}",
                    status_code=503,
                    code="connection_error",
                ) from exc

            if response.status_code < 400:
                response_data = response.json()
                candidates = response_data.get("candidates") or []
                if not candidates:
                    raise OpenAIServiceError(
                        "Gemini retornou resposta vazia.",
                        status_code=502,
                        code="invalid_response",
                    )
                parts_out = candidates[0].get("content", {}).get("parts") or []
                content = "".join(
                    str(part.get("text") or "") for part in parts_out if isinstance(part, dict)
                ).strip()
                if not content:
                    raise OpenAIServiceError(
                        "Gemini retornou conteúdo vazio.",
                        status_code=502,
                        code="invalid_response",
                    )
                return content, f"gemini:{model}"

            last_error_body = response.text
            if response.status_code in (404, 429, 500, 502, 503, 504):
                continue
            raise OpenAIServiceError(
                f"Erro do Gemini (status {response.status_code}): {response.text}",
                status_code=502,
                code="provider_error",
            )

    raise OpenAIServiceError(
        (
            "Nenhum modelo Gemini respondeu à extração. "
            f"Modelos tentados: {', '.join(attempted_models)}. "
            f"Último erro: {last_error_body}"
        ),
        status_code=503,
        code="provider_error",
    )


def _get_client() -> AsyncOpenAI:
    return AsyncOpenAI(
        api_key=get_openai_api_key(),
        timeout=OPENAI_ORCAMENTO_TIMEOUT_SECONDS,
    )


def _parse_json_content(content: str) -> Dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, count=1).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


import base64

def _pdf_page_to_base64_image(
    pdf_content: bytes,
    page_number: int,
    *,
    resolution: int = 150,
) -> str:
    """Converte uma página do PDF para uma imagem base64 JPEG."""
    with pdfplumber.open(BytesIO(pdf_content)) as pdf:
        if page_number < 1 or page_number > len(pdf.pages):
            raise ValueError(f"Página {page_number} inválida.")
        page = pdf.pages[page_number - 1]
        im = page.to_image(resolution=resolution).original
        buffered = BytesIO()
        im.convert("RGB").save(buffered, format="JPEG")
        return base64.b64encode(buffered.getvalue()).decode("utf-8")
def _extract_page_snippets(pdf_content: bytes, max_pages: int = _MAX_DETECTION_PAGES) -> List[Dict[str, Any]]:
    snippets: List[Dict[str, Any]] = []
    with pdfplumber.open(BytesIO(pdf_content)) as pdf:
        for index, page in enumerate(pdf.pages[:max_pages], start=1):
            text = (page.extract_text() or "").strip()
            if not text:
                continue
            snippets.append(
                {
                    "page": index,
                    "text": text[:_MAX_DETECTION_CHARS_PER_PAGE],
                }
            )
    return snippets


def _build_detection_prompt(page_snippets: List[Dict[str, Any]]) -> str:
    payload = {**DETECTION_USER_PROMPT_TEMPLATE, "páginas_iniciais": page_snippets}
    return json.dumps(payload, ensure_ascii=False)


def _build_selected_table_prompt(
    table_rows: List[List[Any]],
    table_page: int,
    table_id: str,
    table_name: str | None = None,
    column_structure: Dict[str, Any] | None = None,
) -> str:
    payload: Dict[str, Any] = {
        "tarefa": "extrair_orcamento_analitico",
        "table_id": table_id,
        "nome_tabela_sugerido": table_name,
        "num_pagina": table_page,
        "observacao": (
            "Use apenas o contexto da tabela selecionada. Se o arquivo tiver cabeçalhos/rodapés "
            "repetidos, ignore-os. Os índices em colunas_detectadas indicam a coluna correta de "
            "cada campo (0 = primeira coluna)."
        ),
        "linhas": truncate_rows_for_audit(
            table_rows,
            max_rows=_MAX_TABLE_ROWS_FOR_PROMPT,
            max_cell_len=_MAX_CELL_CHARS,
        ),
    }
    if column_structure:
        indices = column_structure.get("column_indices") or {}
        if indices:
            payload["colunas_detectadas"] = indices
            payload["linha_cabecalho"] = column_structure.get("header_row_index")
            payload["instrucao_colunas"] = (
                "Um parser local já mapeou as colunas — alinhe quantidade, unidade, BDI e preços "
                "a esses índices. Foque em codigo, descricao, tipo_linha e item_numero."
            )
    return json.dumps(payload, ensure_ascii=False)


def _normalize_table_candidates(raw_tables: Any) -> List[Dict[str, Any]]:
    if isinstance(raw_tables, dict):
        raw_tables = raw_tables.get("tables") or raw_tables.get("options") or []
    if not isinstance(raw_tables, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_tables, start=1):
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                "id": str(item.get("id") or f"table-{index}"),
                "nome_tabela": str(
                    item.get("nome_tabela")
                    or item.get("name")
                    or item.get("titulo")
                    or f"Tabela {index}"
                ),
                "num_pagina": int(item.get("num_pagina") or item.get("page") or 1),
                "preview_texto": str(item.get("preview_texto") or item.get("preview") or "").strip(),
            }
        )
    return normalized


def _coerce_number(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    text = text.replace("R$", "").replace("$", "").replace(" ", "")
    if "." in text and "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _coerce_bdi(value: Any) -> float:
    """Converte BDI com ou sem '%' (ex: '24,53%' -> 24.53)."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "").replace(" ", "")
    return _coerce_number(text)


def _apply_line_sanity_check(
    quantidade: float,
    valor_unitario: float,
    valor_total: float,
) -> tuple[float, float, float]:
    """Corrige desalinhamento típico entre Qtde, Preço Unit. e Preço total."""
    if quantidade <= 0:
        return quantidade, valor_unitario, valor_total

    if valor_unitario <= 0 and valor_total > 0:
        valor_unitario = valor_total / quantidade

    if valor_total <= 0 and valor_unitario > 0:
        valor_total = quantidade * valor_unitario
        return quantidade, valor_unitario, valor_total

    if valor_unitario <= 0:
        return quantidade, valor_unitario, valor_total

    esperado = quantidade * valor_unitario
    if valor_total <= 0:
        return quantidade, valor_unitario, esperado

    erro_relativo = abs(valor_total - esperado) / max(abs(esperado), abs(valor_total), 1.0)
    if erro_relativo <= 0.02:
        return quantidade, valor_unitario, valor_total

    # Total do PDF costuma estar correto; realinha unitário
    if valor_total < esperado * 0.5 or valor_total > esperado * 2.0:
        vu_corrigido = valor_total / quantidade
        if vu_corrigido > 0:
            return quantidade, vu_corrigido, valor_total

    return quantidade, valor_unitario, valor_total


def _items_missing_prices(items: List[Dict[str, Any]]) -> bool:
    """True quando a maioria dos itens executivos não tem preço unitário nem total."""
    executive = [
        it
        for it in items
        if str(it.get("tipo") or "item").lower() != "grupo"
        and "total do grupo" not in str(it.get("descricao") or "").lower()
    ]
    if not executive:
        return False
    missing = 0
    for it in executive:
        vu = _coerce_number(it.get("valor_unitario"))
        vt = _coerce_number(it.get("valor_total"))
        if vu <= 0 and vt <= 0:
            missing += 1
    return missing >= max(1, len(executive) // 2)


async def _extract_with_openai_vision(
    client: AsyncOpenAI,
    *,
    system_msg: str,
    user_msg: str,
    base64_image: str,
    image_mime: str = "image/jpeg",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str]:
    """Executa extração estruturada com imagem + texto de apoio."""
    response = await client.chat.completions.create(
        model=OPENAI_ORCAMENTO_MODEL,
        temperature=0.0,
        max_tokens=8192,
        response_format={"type": "json_schema", "json_schema": EXTRACTION_JSON_SCHEMA},
        messages=[
            {"role": "system", "content": system_msg},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"{EXTRACTION_USER_PROMPT_HEADER}\n\n"
                            f"CONTEXTO DE TEXTO EXTRAÍDO (pode estar incompleto — a imagem é a fonte da verdade para preços):\n"
                            f"{user_msg}"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_mime};base64,{base64_image}",
                            "detail": "high",
                        },
                    },
                ],
            },
        ],
    )
    raw_content = response.choices[0].message.content or "{}"
    if raw_content.startswith("```"):
        raw_content = raw_content.strip("`").removeprefix("json").strip()
    parsed = json.loads(raw_content)
    raw_items = parsed.get("orcamento_itens") if isinstance(parsed, dict) else []
    normalized_items = _normalize_structured_items(raw_items)
    return normalized_items, parsed if isinstance(parsed, dict) else {}, raw_content


async def _extract_with_gemini_vision(
    *,
    system_msg: str,
    user_msg: str,
    base64_image: str | None = None,
    image_mime: str = "image/jpeg",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], str, str]:
    if base64_image:
        user_text = (
            f"{EXTRACTION_USER_PROMPT_HEADER}\n\n"
            "CONTEXTO DE TEXTO EXTRAÍDO (pode estar incompleto — a imagem é a fonte da verdade para preços):\n"
            f"{user_msg}"
        )
    else:
        user_text = f"{EXTRACTION_USER_PROMPT_HEADER}\n\nCONTEXTO DA TABELA:\n{user_msg}"

    raw_content, model_label = await _call_gemini_json(
        system_msg=system_msg,
        user_text=user_text,
        base64_image=base64_image,
        image_mime=image_mime,
    )
    parsed = _parse_json_content(raw_content)
    raw_items = parsed.get("orcamento_itens") if isinstance(parsed, dict) else []
    normalized_items = _normalize_structured_items(raw_items)
    return normalized_items, parsed if isinstance(parsed, dict) else {}, raw_content, model_label


def _resolve_tipo_linha(item: Dict[str, Any]) -> str:
    tipo = str(
        item.get("tipo_linha")
        or item.get("tipo")
        or item.get("Tipo")
        or "item"
    ).strip().lower()
    if tipo in ("grupo", "titulo", "título", "title"):
        return "grupo"
    if tipo in ("composicao", "composição", "insumo", "subitem"):
        return "composicao"
    return "item"


def _resolve_item_numero(item: Dict[str, Any]) -> str:
    return str(
        item.get("item_numero")
        or item.get("item")
        or item.get("Item")
        or ""
    ).strip()


def _coerce_row_fields(item: Dict[str, Any]) -> Dict[str, Any]:
    item_number = _resolve_item_numero(item)
    tipo_linha = _resolve_tipo_linha(item)
    banco = item.get("banco") or ""
    codigo = str(item.get("codigo") or item.get("Código") or item.get("code") or "").strip()
    descricao = item.get("descricao") or item.get("Descrição") or item.get("description") or ""
    descricao = str(descricao).strip()
    bdi = _coerce_bdi(item.get("bdi") or item.get("BDI") or item.get("bdi_percentual"))
    unidade = item.get("unidade") or item.get("Unidade") or item.get("unit") or "un"
    quantidade = _coerce_number(
        item.get("quantidade") or item.get("Quantidade") or item.get("qty")
    )
    valor_unitario = _coerce_number(
        item.get("valor_unitario")
        or item.get("Valor Unitário")
        or item.get("valor_unitário")
        or item.get("unit_value")
    )
    total = _coerce_number(item.get("valor_total") or item.get("Total") or item.get("total"))
    quantidade, valor_unitario, total = _apply_line_sanity_check(
        quantidade, valor_unitario, total
    )
    grupo_val = str(
        item.get("grupo") or item.get("Grupo") or item.get("grupo_hierarquico") or ""
    ).strip()

    rotulo = str(item.get("rotulo_linha") or "").strip() or None
    tipo_categoria = str(
        item.get("tipo_categoria") or item.get("tipo_item") or item.get("Tipo") or ""
    ).strip() or None
    porcentagem = _coerce_number(item.get("porcentagem") or item.get("Porcent.") or item.get("percentual"))

    if rotulo and not item_number:
        rotulo_lower = rotulo.lower()
        if rotulo_lower in ("composição", "composicao", "composição auxiliar", "composicao auxiliar", "insumo"):
            tipo_linha = "composicao"

    # Grupo/item definitivo é aplicado em analitico_normalize (qtd e VU zerados → grupo)
    row_out = {
        "item": item_number,
        "item_numero": item_number,
        "tipo": tipo_linha,
        "tipo_linha": tipo_linha,
        "rotulo_linha": rotulo,
        "tipo_categoria": tipo_categoria,
        "porcentagem": porcentagem,
        "banco": str(banco).strip(),
        "codigo": codigo,
        "grupo": grupo_val or None,
        "descricao": descricao,
        "bdi": bdi,
        "unidade": str(unidade).strip(),
        "quantidade": quantidade,
        "valor_unitario": valor_unitario,
        "valor_total": total,
    }
    if item.get("confianca") is not None:
        row_out["confianca"] = item.get("confianca")
    if item.get("alertas"):
        row_out["alertas"] = item.get("alertas")
    if item.get("origem_extracao"):
        row_out["origem_extracao"] = item.get("origem_extracao")
    return row_out


def _should_skip_extracted_row(tipo: str, descricao: str, codigo: str) -> bool:
    if tipo in ("grupo", "composicao"):
        return False
    desc_lower = descricao.lower()
    if "total do grupo" in desc_lower:
        return True
    if desc_lower.startswith("total ") and not codigo:
        return True
    return False


def _should_skip_hierarchical_row(
    tipo: str,
    descricao: str,
    *,
    quantidade: float = 0.0,
    valor_total: float = 0.0,
) -> bool:
    desc_lower = descricao.lower().strip()
    if not descricao and tipo == "item":
        if quantidade <= 0 and valor_total <= 0:
            return True
    junk_markers = (
        "item | descrição",
        "código | descrição",
        "codigo | descricao",
    )
    if any(marker in desc_lower for marker in junk_markers):
        return True
    return False


def _normalize_hierarchical_items(raw_items: Any) -> List[Dict[str, Any]]:
    """Preserva ordem original e todas as linhas hierárquicas (grupo, item, composição)."""
    if not isinstance(raw_items, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        row = _coerce_row_fields(item)
        if _should_skip_hierarchical_row(
            row["tipo_linha"],
            row["descricao"],
            quantidade=float(row.get("quantidade") or 0),
            valor_total=float(row.get("valor_total") or 0),
        ):
            continue
        normalized.append(row)
    return normalize_hierarchical_analitico(normalized)


def _normalize_structured_items(raw_items: Any) -> List[Dict[str, Any]]:
    if not isinstance(raw_items, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for index, item in enumerate(raw_items, start=1):
        if not isinstance(item, dict):
            continue

        row = _coerce_row_fields(item)
        if _should_skip_extracted_row(row["tipo_linha"], row["descricao"], row["codigo"]):
            continue
        if row["tipo_linha"] != "item":
            continue

        normalized.append(row)
    return normalized


def _build_summary(items: List[Dict[str, Any]], model_name: str) -> Dict[str, Any]:
    return {
        "total_items": len(items),
        "valor_total": sum(float(item.get("valor_total") or 0) for item in items),
        "confianca": 0.82 if items else 0.0,
        "metodo": model_name,
    }


async def identify_tables(pdf_content: bytes) -> List[Dict[str, Any]]:
    """
    Identifica tabelas candidatas usando apenas as páginas iniciais do PDF.
    Retorna lista de objetos com id, nome_tabela, num_pagina e preview_texto.
    """
    provider = _resolve_extraction_provider()
    t0 = time.perf_counter()
    page_snippets = _extract_page_snippets(pdf_content)
    if not page_snippets:
        raise OpenAIServiceError(
            "Não foi possível extrair texto das páginas iniciais do PDF.",
            status_code=422,
            code="no_text_found",
        )

    system_msg = DETECTION_SYSTEM_PROMPT
    user_msg = _build_detection_prompt(page_snippets)
    input_audit = {
        "pages": [{"page": snippet["page"], "chars": len(snippet["text"])} for snippet in page_snippets],
        "provider": provider,
    }

    try:
        if provider == "gemini":
            raw_content, model_label = await _call_gemini_json(
                system_msg=system_msg,
                user_text=user_msg,
            )
            provider_name = "gemini"
            model_name = model_label.split(":", 1)[-1]
        else:
            client = _get_client()
            response = await client.chat.completions.create(
                model=OPENAI_ORCAMENTO_MODEL,
                temperature=0,
                max_tokens=900,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            )
            raw_content = response.choices[0].message.content or "{}"
            provider_name = "openai"
            model_name = OPENAI_ORCAMENTO_MODEL

        duration_ms = (time.perf_counter() - t0) * 1000
        parsed = _parse_json_content(raw_content)
        candidates = _normalize_table_candidates(parsed)

        # Further filter candidates using local heuristics to avoid sending edital pages
        try:
            budget_candidates = detect_budget_pages(pdf_content, max_pages=_MAX_DETECTION_PAGES)
            allowed_pages = {c.page_number for c in budget_candidates}
            filtered = [c for c in candidates if c.get("num_pagina") in allowed_pages]
            if filtered:
                candidates = filtered
            else:
                # If none passed the local filter, return empty to avoid false positives
                candidates = []
        except Exception:
            # On any detection error, fall back to original candidates (conservative)
            logger.exception("Erro ao aplicar filtro local de budget pages")

        log_ai_exchange(
            operation="identify_tables",
            provider=provider_name,
            model=model_name,
            input_payload=input_audit,
            output_payload={
                "tables_found": len(candidates),
                "tables_preview": [
                    {"id": table["id"], "nome_tabela": table["nome_tabela"], "num_pagina": table["num_pagina"]}
                    for table in candidates[:10]
                ],
                "raw_chars": len(raw_content),
            },
            duration_ms=duration_ms,
        )
        return candidates

    except OpenAIServiceError:
        raise
    except RateLimitError as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="identify_tables",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"rate_limit: {exc}",
            duration_ms=duration_ms,
        )
        raise OpenAIServiceError("Limite de requisições da OpenAI atingido. Tente novamente em instantes.", status_code=429, code="rate_limit") from exc
    except (APIConnectionError, APITimeoutError) as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="identify_tables",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"connection: {exc}",
            duration_ms=duration_ms,
        )
        raise OpenAIServiceError("Falha de conexão com a OpenAI. Tente novamente.", status_code=503, code="connection_error") from exc
    except BadRequestError as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="identify_tables",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"bad_request: {exc}",
            duration_ms=duration_ms,
        )
        raise OpenAIServiceError("A requisição para a OpenAI foi rejeitada. Verifique o prompt ou a chave.", status_code=400, code="bad_request") from exc
    except (json.JSONDecodeError, OpenAIError, ValueError) as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="identify_tables",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"parse_or_openai: {exc}",
            duration_ms=duration_ms,
        )
        raise OpenAIServiceError("A OpenAI retornou uma resposta inválida ao detectar tabelas.", status_code=502, code="invalid_response") from exc


async def process_selected_table(
    pdf_content: bytes,
    table_id: str,
    *,
    table_rows: List[List[Any]],
    table_page: int,
    table_name: str | None = None,
    table_image_base64: str | None = None,
    user_id: str | None = None,
) -> Tuple[Dict[str, Any], str]:
    """
    LEGADO — não usar no path principal.

    A extração de itens passou para `app.domain.budget_pipeline.run_pipeline`
    (5 engines determinísticos). OpenAI/Gemini vision aqui só permanece para
    `_legacy_main` e experimentos; insights textual usam
    `budget_pipeline.insights_openai` sobre JSON já validado.
    """
    provider = _resolve_extraction_provider()
    t0 = time.perf_counter()

    table_structure = detect_table_structure(table_rows)
    from services.extraction_learning import build_extraction_system_prompt

    system_msg = build_extraction_system_prompt(user_id, EXTRACTION_SYSTEM_PROMPT)
    user_msg = _build_selected_table_prompt(
        table_rows,
        table_page,
        table_id,
        table_name,
        column_structure=table_structure,
    )
    
    image_mime = "image/png"
    if table_image_base64:
        base64_image = table_image_base64.strip()
    else:
        try:
            base64_image = _pdf_page_to_base64_image(pdf_content, table_page)
            image_mime = "image/jpeg"
        except Exception as e:
            logger.warning(f"Falha ao gerar imagem da página {table_page}: {e}")
            base64_image = None

    input_audit = {
        "table_id": table_id,
        "table_name": table_name,
        "page": table_page,
        "rows_count": len(table_rows),
        "rows_truncated": truncate_rows_for_audit(table_rows, max_rows=10),
        "has_image": bool(base64_image),
        "image_source": "crop" if table_image_base64 else "full_page",
        "column_indices": table_structure.get("column_indices"),
        "parser_items_found": len(table_structure.get("parser_items") or []),
        "provider": provider,
    }

    provider_name = provider
    model_name = OPENAI_ORCAMENTO_MODEL

    try:
        if provider == "gemini":
            normalized_items, parsed, raw_content, model_label = await _extract_with_gemini_vision(
                system_msg=system_msg,
                user_msg=user_msg,
                base64_image=base64_image,
                image_mime=image_mime,
            )
            provider_name = "gemini"
            model_name = model_label.split(":", 1)[-1]
        else:
            client = _get_client()
            messages = [
                {"role": "system", "content": system_msg},
            ]
            
            if base64_image:
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"{EXTRACTION_USER_PROMPT_HEADER}\n\nCONTEXTO DE TEXTO EXTRAÍDO (Pode conter erros de alinhamento, use a imagem como fonte da verdade):\n{user_msg}"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_mime};base64,{base64_image}",
                                "detail": "high"
                            }
                        }
                    ]
                })
            else:
                messages.append({
                    "role": "user",
                    "content": f"{EXTRACTION_USER_PROMPT_HEADER}\n\nCONTEXTO DA TABELA:\n{user_msg}"
                })

            response = await client.chat.completions.create(
                model=OPENAI_ORCAMENTO_MODEL,
                temperature=0.0,
                max_tokens=8192,
                response_format={"type": "json_schema", "json_schema": EXTRACTION_JSON_SCHEMA},
                messages=messages,
            )
            raw_content = response.choices[0].message.content or "{}"
            
            if raw_content.startswith("```"):
                raw_content = raw_content.strip("`").removeprefix("json").strip()
                
            try:
                parsed = json.loads(raw_content)
            except json.JSONDecodeError as e:
                print("CONTEÚDO QUE FALHOU NO PARSE:", raw_content)
                import traceback; traceback.print_exc()
                raise ValueError(f"Falha ao decodificar o JSON retornado pela OpenAI: {e}") from e
            raw_items = parsed.get("orcamento_itens") if isinstance(parsed, dict) else []
            merged_raw = merge_ai_with_parser(
                [item for item in raw_items if isinstance(item, dict)],
                table_structure,
            )
            hierarchical_items = enrich_hierarchical_items(
                _normalize_hierarchical_items(merged_raw or raw_items)
            )
            normalized_items = _normalize_structured_items(merged_raw or raw_items)
            if not normalized_items and hierarchical_items:
                normalized_items = [
                    row for row in hierarchical_items if row.get("tipo_linha") == "item"
                ]

        duration_ms = (time.perf_counter() - t0) * 1000

        if provider == "gemini":
            raw_items = parsed.get("orcamento_itens") if isinstance(parsed, dict) else []
            merged_raw = merge_ai_with_parser(
                [item for item in raw_items if isinstance(item, dict)],
                table_structure,
            )
            hierarchical_items = enrich_hierarchical_items(
                _normalize_hierarchical_items(merged_raw or raw_items)
            )
            normalized_items = _normalize_structured_items(merged_raw or raw_items)
            if not normalized_items and hierarchical_items:
                normalized_items = [
                    row for row in hierarchical_items if row.get("tipo_linha") == "item"
                ]

        if not normalized_items and raw_content:
            logger.warning(
                "%s retornou 0 itens úteis para %s (pág %s). raw_chars=%s snippet=%s",
                provider_name,
                table_id,
                table_page,
                len(raw_content),
                raw_content[:200],
            )

        needs_full_page_retry = (
            table_image_base64
            and (
                not normalized_items
                or _items_missing_prices(normalized_items)
            )
        )
        if needs_full_page_retry:
            try:
                full_page_b64 = _pdf_page_to_base64_image(pdf_content, table_page)
                logger.info(
                    "Retentando %s (pág %s) com imagem da página inteira (itens=%s, sem_preço=%s)",
                    table_id,
                    table_page,
                    len(normalized_items),
                    _items_missing_prices(normalized_items),
                )
                if provider == "gemini":
                    retry_items, retry_parsed, retry_raw, retry_model = await _extract_with_gemini_vision(
                        system_msg=system_msg,
                        user_msg=user_msg,
                        base64_image=full_page_b64,
                        image_mime="image/jpeg",
                    )
                    model_name = retry_model.split(":", 1)[-1]
                else:
                    retry_items, retry_parsed, retry_raw = await _extract_with_openai_vision(
                        client,
                        system_msg=system_msg,
                        user_msg=user_msg,
                        base64_image=full_page_b64,
                        image_mime="image/jpeg",
                    )
                if retry_items and (
                    not normalized_items
                    or not _items_missing_prices(retry_items)
                    or len(retry_items) > len(normalized_items)
                ):
                    parsed = retry_parsed
                    raw_content = retry_raw
                    retry_raw_items = (
                        retry_parsed.get("orcamento_itens")
                        if isinstance(retry_parsed, dict)
                        else []
                    )
                    retry_merged = merge_ai_with_parser(
                        [item for item in retry_raw_items if isinstance(item, dict)],
                        table_structure,
                    )
                    hierarchical_items = enrich_hierarchical_items(
                        _normalize_hierarchical_items(retry_merged or retry_raw_items)
                    )
                    normalized_items = _normalize_structured_items(retry_merged or retry_raw_items)
                    input_audit["image_source"] = "full_page_retry"
                    duration_ms = (time.perf_counter() - t0) * 1000
            except Exception as retry_exc:
                logger.warning("Retry página inteira falhou para %s: %s", table_id, retry_exc)

        summary = parsed.get("resumo") if isinstance(parsed, dict) else {}
        if not isinstance(summary, dict):
            summary = _build_summary(normalized_items, model_name)
        else:
            summary = {
                "total_items": int(summary.get("total_items") or len(normalized_items)),
                "valor_total": float(summary.get("valor_total") or sum(float(item.get("valor_total") or 0) for item in normalized_items)),
                "confianca": float(summary.get("confianca") or 0.82),
                "metodo": str(summary.get("metodo") or model_name),
            }

        structured_output = {
            "items": normalized_items,
            "hierarchical_items": hierarchical_items,
            "resumo": summary,
        }

        log_ai_exchange(
            operation="process_selected_table",
            provider=provider_name,
            model=model_name,
            input_payload=input_audit,
            output_payload={
                "items_count": len(normalized_items),
                "raw_items_count": len(structured_output["items"]),
                "resumo": summary,
                "raw_chars": len(raw_content),
            },
            duration_ms=duration_ms,
        )
        return structured_output, f"{provider_name}:{model_name}"

    except OpenAIServiceError:
        raise
    except RateLimitError as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="process_selected_table",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"rate_limit: {exc}",
            duration_ms=duration_ms,
        )
        raise OpenAIServiceError("Limite de requisições da OpenAI atingido. Tente novamente em instantes.", status_code=429, code="rate_limit") from exc
    except (APIConnectionError, APITimeoutError) as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="process_selected_table",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"connection: {exc}",
            duration_ms=duration_ms,
        )
        raise OpenAIServiceError("Falha de conexão com a OpenAI. Tente novamente.", status_code=503, code="connection_error") from exc
    except BadRequestError as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="process_selected_table",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"bad_request: {exc}",
            duration_ms=duration_ms,
        )
        print("OPENAI BAD REQUEST ERROR:", exc)
        import traceback; traceback.print_exc()
        raise OpenAIServiceError(f"A requisição para a OpenAI foi rejeitada: {exc}", status_code=400, code="bad_request") from exc
    except (json.JSONDecodeError, OpenAIError, ValueError) as exc:
        duration_ms = (time.perf_counter() - t0) * 1000
        log_ai_exchange(
            operation="process_selected_table",
            provider="openai",
            model=OPENAI_ORCAMENTO_MODEL,
            input_payload=input_audit,
            error=f"parse_or_openai: {exc}",
            duration_ms=duration_ms,
        )
        print("OPENAI PARSE/RESPONSE ERROR:", exc)
        import traceback; traceback.print_exc()
        raise OpenAIServiceError(f"A OpenAI retornou uma resposta inválida: {exc}", status_code=502, code="invalid_response") from exc


def _extract_page_text(pdf_content: bytes, page_number: int, max_chars: int = 6000) -> str:
    with pdfplumber.open(BytesIO(pdf_content)) as pdf:
        if page_number < 1 or page_number > len(pdf.pages):
            return ""
        return (pdf.pages[page_number - 1].extract_text() or "")[:max_chars]


def _detect_budget_page_candidates(
    pdf_content: bytes,
    max_pages: int = _MAX_FULL_PDF_PAGES,
) -> List[BudgetPageCandidate]:
    return detect_budget_pages(pdf_content, max_pages=max_pages)


async def _extract_analitico_from_page(
    client: AsyncOpenAI,
    *,
    pdf_content: bytes,
    page_number: int,
    total_pages: int,
    image_detail: str = "high",
    textual_mode: bool = False,
    aggressive: bool = False,
) -> List[Dict[str, Any]]:
    max_chars = 12000 if textual_mode else 8000
    page_text = _extract_page_text(pdf_content, page_number, max_chars=max_chars)
    base64_image: str | None = None
    if image_detail != "text":
        try:
            img_res = 200 if textual_mode or aggressive else 150
            base64_image = _pdf_page_to_base64_image(
                pdf_content, page_number, resolution=img_res
            )
        except Exception as exc:
            logger.warning("Imagem indisponível pág %s: %s", page_number, exc)
            base64_image = None

    user_text = FULL_PDF_PAGE_USER_TEMPLATE.format(
        page=page_number,
        total_pages=total_pages,
        text_snippet=page_text or "(sem texto extraído)",
    )
    if textual_mode:
        user_text += FULL_PDF_TEXTUAL_RETRY_ADDON
    if aggressive:
        user_text += FULL_PDF_AGGRESSIVE_RETRY_ADDON

    vision_detail = "high" if image_detail in ("high", "text") or textual_mode else "low"
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": ANALITICO_FULL_SYSTEM_PROMPT},
    ]
    if base64_image:
        messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}",
                            "detail": vision_detail,
                        },
                    },
                ],
            }
        )
    else:
        messages.append({"role": "user", "content": user_text})

    response = await client.chat.completions.create(
        model=OPENAI_ORCAMENTO_MODEL,
        temperature=0.0,
        max_tokens=8192,
        response_format={"type": "json_schema", "json_schema": EXTRACTION_JSON_SCHEMA},
        messages=messages,
    )
    raw_content = response.choices[0].message.content or "{}"
    if raw_content.startswith("```"):
        raw_content = raw_content.strip("`").removeprefix("json").strip()
    parsed = json.loads(raw_content)
    raw_items = parsed.get("orcamento_itens") if isinstance(parsed, dict) else []
    return _normalize_hierarchical_items(raw_items)


async def _extract_analitico_page_with_retries(
    client: AsyncOpenAI,
    *,
    pdf_content: bytes,
    page_number: int,
    total_pages: int,
) -> List[Dict[str, Any]]:
    """Sempre alta resolução + modo texto; retenta se a primeira passagem vier vazia."""
    page_items = await _extract_analitico_from_page(
        client,
        pdf_content=pdf_content,
        page_number=page_number,
        total_pages=total_pages,
        image_detail="high",
        textual_mode=True,
    )
    if page_items:
        return page_items

    if _ANALITICO_PAGE_DELAY_SECONDS > 0:
        await asyncio.sleep(_ANALITICO_PAGE_DELAY_SECONDS)

    logger.info(
        "PDF analítico pág %s: retentativa agressiva (texto + imagem)",
        page_number,
    )
    return await _extract_analitico_from_page(
        client,
        pdf_content=pdf_content,
        page_number=page_number,
        total_pages=total_pages,
        image_detail="high",
        textual_mode=True,
        aggressive=True,
    )


def _extract_document_metadata(
    hierarchical_items: List[Dict[str, Any]],
    filename: str | None = None,
) -> Dict[str, Any]:
    bancos = sorted(
        {
            str(item.get("banco") or "").strip()
            for item in hierarchical_items
            if str(item.get("banco") or "").strip()
        }
    )
    bdi_values = [
        float(item.get("bdi") or 0)
        for item in hierarchical_items
        if float(item.get("bdi") or 0) > 0
    ]
    obra = (filename or "").replace(".pdf", "").replace(".PDF", "").strip() or None
    for item in hierarchical_items[:30]:
        desc = str(item.get("descricao") or "")
        if item.get("tipo_linha") == "grupo" and len(desc) > 20:
            obra = desc[:240]
            break

    return {
        "nome_obra": obra,
        "bancos_referencia": "\n".join(bancos) if bancos else "SINAPI / SICRO",
        "bdi_percent": sum(bdi_values) / len(bdi_values) if bdi_values else None,
        "encargos_sociais": None,
    }


def _emit_progress(
    callback: Optional[ProgressCallback],
    payload: Dict[str, Any],
) -> None:
    if callback is None:
        return
    try:
        callback(payload)
    except Exception as exc:
        logger.debug("progress callback: %s", exc)


async def process_full_pdf_analitico(
    pdf_content: bytes,
    *,
    filename: str | None = None,
    max_pages: int = _MAX_FULL_PDF_PAGES,
    progress_callback: Optional[ProgressCallback] = None,
) -> Tuple[Dict[str, Any], str]:
    """
    Processa o PDF inteiro página a página (sem seleção de tabelas).
    Retorna itens hierárquicos na ordem do documento.
    """
    _ensure_api_key()
    t0 = time.perf_counter()
    client = _get_client()
    primary_candidates = _detect_budget_page_candidates(pdf_content, max_pages=max_pages)
    page_candidates = list(primary_candidates)
    if not page_candidates:
        page_candidates = detect_readable_pages_for_rescan(
            pdf_content, max_pages=max_pages
        )
    if not page_candidates:
        raise OpenAIServiceError(
            "Não foi possível ler páginas do PDF.",
            status_code=422,
            code="no_pages",
        )

    total_pages = len(page_candidates)
    scanned_pages: set[int] = set()
    _emit_progress(
        progress_callback,
        {
            "phase": "started",
            "pages_total": total_pages,
            "pages_done": 0,
            "current_page": None,
            "message": f"Preparando análise de {total_pages} página(s)…",
        },
    )

    combined_hierarchical: List[Dict[str, Any]] = []
    pages_meta: List[Dict[str, Any]] = []

    for index, candidate in enumerate(page_candidates):
        page_num = candidate.page_number
        if index > 0 and _ANALITICO_PAGE_DELAY_SECONDS > 0:
            await asyncio.sleep(_ANALITICO_PAGE_DELAY_SECONDS)

        _emit_progress(
            progress_callback,
            {
                "phase": "processing",
                "pages_total": total_pages,
                "pages_done": index,
                "current_page": page_num,
                "message": f"Analisando página {page_num} ({index + 1}/{total_pages})…",
            },
        )

        scanned_pages.add(page_num)
        try:
            page_items = await _extract_analitico_page_with_retries(
                client,
                pdf_content=pdf_content,
                page_number=page_num,
                total_pages=total_pages,
            )
            for item in page_items:
                item["_source_page"] = page_num
            combined_hierarchical.extend(page_items)
            pages_meta.append(
                {
                    "page": page_num,
                    "items": len(page_items),
                    "image_detail": candidate.image_detail,
                    "table_score": candidate.table_score,
                    "text_score": candidate.text_score,
                }
            )
            logger.info(
                "PDF analítico pág %s (%s, texto=%s): %s linhas",
                page_num,
                candidate.image_detail,
                candidate.text_score,
                len(page_items),
            )
        except Exception as exc:
            logger.warning("Falha ao extrair pág %s: %s", page_num, exc)
            pages_meta.append({"page": page_num, "items": 0, "error": str(exc)})

        _emit_progress(
            progress_callback,
            {
                "phase": "processing",
                "pages_total": total_pages,
                "pages_done": index + 1,
                "current_page": page_num,
                "message": f"Página {page_num} concluída ({index + 1}/{total_pages})",
            },
        )

    if not combined_hierarchical:
        logger.warning(
            "PDF analítico: varredura ampla — nenhuma linha na 1ª passagem (%s págs)",
            total_pages,
        )
        rescan_candidates = [
            c
            for c in detect_readable_pages_for_rescan(pdf_content, max_pages=max_pages)
            if c.page_number not in scanned_pages
        ]
        if not rescan_candidates:
            rescan_candidates = detect_readable_pages_for_rescan(
                pdf_content, max_pages=max_pages
            )
        rescan_total = len(rescan_candidates)
        combined_total = total_pages + rescan_total
        _emit_progress(
            progress_callback,
            {
                "phase": "processing",
                "pages_total": combined_total,
                "pages_done": total_pages,
                "message": f"Varredura ampla em {rescan_total} página(s) adicional(is)…",
            },
        )
        for r_index, candidate in enumerate(rescan_candidates):
            page_num = candidate.page_number
            if r_index > 0 and _ANALITICO_PAGE_DELAY_SECONDS > 0:
                await asyncio.sleep(_ANALITICO_PAGE_DELAY_SECONDS)

            progress_done = total_pages + r_index
            _emit_progress(
                progress_callback,
                {
                    "phase": "processing",
                    "pages_total": combined_total,
                    "pages_done": progress_done,
                    "current_page": page_num,
                    "message": (
                        f"Varredura ampla: página {page_num} "
                        f"({progress_done + 1}/{combined_total})…"
                    ),
                },
            )
            try:
                page_items = await _extract_analitico_page_with_retries(
                    client,
                    pdf_content=pdf_content,
                    page_number=page_num,
                    total_pages=combined_total,
                )
                for item in page_items:
                    item["_source_page"] = page_num
                combined_hierarchical.extend(page_items)
                pages_meta.append(
                    {
                        "page": page_num,
                        "items": len(page_items),
                        "pass": "broad_rescan",
                    }
                )
                _emit_progress(
                    progress_callback,
                    {
                        "phase": "processing",
                        "pages_total": combined_total,
                        "pages_done": total_pages + r_index + 1,
                        "current_page": page_num,
                        "message": (
                            f"Varredura ampla concluída: página {page_num} "
                            f"({total_pages + r_index + 1}/{combined_total})"
                        ),
                    },
                )
            except Exception as exc:
                logger.warning("Varredura ampla pág %s: %s", page_num, exc)

    if not combined_hierarchical:
        logger.warning(
            "PDF analítico sem linhas — páginas: %s",
            pages_meta,
        )
        raise OpenAIServiceError(
            "Nenhuma linha orçamentária encontrada no PDF. "
            f"Foram analisadas {total_pages} página(s) em tabelas, textos e imagens, "
            "sem itens com quantidade ou valores. "
            "Confira se o anexo contém serviços com preços/quantidades legíveis.",
            status_code=422,
            code="no_budget_lines",
        )

    combined_hierarchical = _normalize_hierarchical_items(combined_hierarchical)

    normalized_items = [
        row
        for row in combined_hierarchical
        if row.get("tipo_linha") == "item"
        and not _should_skip_extracted_row(
            "item",
            str(row.get("descricao") or ""),
            str(row.get("codigo") or ""),
        )
    ]
    metadata = _extract_document_metadata(combined_hierarchical, filename)
    valor_total = sum(float(item.get("valor_total") or 0) for item in normalized_items)
    summary = {
        "total_items": len(normalized_items),
        "total_linhas": len(combined_hierarchical),
        "valor_total": valor_total,
        "paginas_processadas": total_pages,
        "paginas_candidatas": [c.page_number for c in page_candidates],
        "confianca": 0.85,
        "metodo": f"{OPENAI_ORCAMENTO_MODEL} (full_pdf)",
        "metadata": metadata,
    }

    structured_output = {
        "items": normalized_items,
        "hierarchical_items": combined_hierarchical,
        "resumo": summary,
        "pages_meta": pages_meta,
    }

    duration_ms = (time.perf_counter() - t0) * 1000
    log_ai_exchange(
        operation="process_full_pdf_analitico",
        provider="openai",
        model=OPENAI_ORCAMENTO_MODEL,
        input_payload={
            "pages": [c.page_number for c in page_candidates],
            "filename": filename,
        },
        output_payload={
            "hierarchical_count": len(combined_hierarchical),
            "executive_count": len(normalized_items),
            "pages_meta": pages_meta,
        },
        duration_ms=duration_ms,
    )
    return structured_output, f"openai:{OPENAI_ORCAMENTO_MODEL} (full_pdf)"


# ---------------------------------------------------------------------------
# Relatórios — chat multi-turno (ChatGPT style)
# ---------------------------------------------------------------------------

REPORT_CHAT_MODEL = os.getenv("OPENAI_REPORT_MODEL", OPENAI_MODEL)
_MAX_REPORT_HISTORY = 24

REPORT_CHAT_JSON_SCHEMA = {
    "name": "report_chat_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "reply": {
                "type": "string",
                "description": "Resposta em Markdown (GFM): tabelas, listas, negrito, código.",
            },
            "chart": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["none", "bar", "pie"],
                        "description": "Use 'none' quando não houver gráfico.",
                    },
                    "title": {"type": "string"},
                    "value_label": {
                        "type": "string",
                        "enum": ["valor", "quantidade", "percentual"],
                    },
                    "data": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {"type": "number"},
                            },
                            "required": ["name", "value"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["type", "title", "value_label", "data"],
                "additionalProperties": False,
            },
        },
        "required": ["reply", "chart"],
        "additionalProperties": False,
    },
}

REPORT_CHAT_SYSTEM_TEMPLATE = """Você é um Engenheiro de Custos Sênior e Analista de Dados especializado em orçamentos de obras públicas e privadas no Brasil.

CONTEXTO DO ORÇAMENTO (JSON validado — fonte única da verdade; NUNCA invente itens ou valores):
{budget_json}

RESUMO CURVA ABC:
{abc_summary}

REGRAS OBRIGATÓRIAS:
1. Responda SOMENTE com base nos dados do JSON acima.
2. O campo "reply" deve usar Markdown com GitHub Flavored Markdown (tabelas com | col |, listas, **negrito**).
3. Quando o usuário pedir TABELA, use OBRIGATORIAMENTE tabela Markdown GFM no campo "reply", com cabeçalho e linha separadora. Exemplo:
| # | Descrição | Qtd. | Valor total |
|---:|---|---:|---:|
| 1 | Serviço X | 10,00 | R$ 150.000,00 |
Nunca substitua tabela por lista numerada quando pedirem "tabela".
4. Respeite a quantidade pedida (ex: "5 itens" → exatamente 5 linhas na tabela).
5. Preencha "chart" APENAS se o usuário pedir gráfico/visualização OU se um gráfico facilitar muito a resposta; caso contrário use type "none", title "", value_label "valor" e data [].
6. Em "chart": type "bar", "pie" ou "none"; data com até 15 pontos; "value" em REAIS quando value_label for "valor" (use valor_total_calculado), NUNCA percentual acumulado 0-100 no eixo de valor.
7. Quando houver gráfico, o campo "reply" deve trazer análise textual completa (2+ parágrafos), insights e tabela Markdown quando fizer sentido — o PDF exporta texto + gráfico juntos.
8. value_label: "quantidade" só para gráficos de quantidade; "percentual" só se o usuário pedir % explicitamente; caso contrário "valor".
9. Mantenha coerência com o histórico da conversa (perguntas anteriores do usuário).
10. Não responda assuntos fora deste orçamento/PDF.
11. Responda sempre em português do Brasil."""


def _build_abc_summary(items: List[Dict[str, Any]]) -> str:
    totals = {"A": 0.0, "B": 0.0, "C": 0.0}
    counts = {"A": 0, "B": 0, "C": 0}
    for item in items:
        cls = str(item.get("classification") or item.get("classificacao") or "").upper()
        if cls not in totals:
            continue
        val = float(item.get("valor_total_calculado") or item.get("lineTotal") or item.get("valor_total") or 0)
        totals[cls] += val
        counts[cls] += 1
    grand = sum(totals.values()) or 1.0
    lines = []
    for cls in ("A", "B", "C"):
        pct = totals[cls] / grand * 100.0
        lines.append(f"- Classe {cls}: {counts[cls]} itens, R$ {totals[cls]:,.2f} ({pct:.1f}% do total)")
    return "\n".join(lines) if lines else "Classificação ABC não disponível."


def _normalize_report_chart(raw_chart: Any) -> Dict[str, Any] | None:
    if raw_chart is None:
        return None
    if not isinstance(raw_chart, dict):
        return None
    chart_type_raw = str(raw_chart.get("type") or raw_chart.get("chart_type") or "").lower()
    if chart_type_raw in ("none", "null", ""):
        return None
    data = raw_chart.get("data") or []
    if not isinstance(data, list) or not data:
        return None
    normalized = []
    for row in data[:15]:
        if not isinstance(row, dict):
            continue
        normalized.append(
            {
                "name": str(row.get("name") or row.get("label") or "-")[:55],
                "value": float(row.get("value") or row.get("valor") or 0),
            }
        )
    if not normalized:
        return None
    chart_type = str(raw_chart.get("type") or raw_chart.get("chart_type") or "bar").lower()
    if chart_type not in ("bar", "pie"):
        chart_type = "bar"
    return {
        "type": chart_type,
        "chart_type": "horizontal_bar" if chart_type == "bar" else "pie",
        "title": str(raw_chart.get("title") or "Gráfico"),
        "value_label": str(raw_chart.get("value_label") or "valor"),
        "data": normalized,
    }


async def generate_report_chat(
    conversation: List[Dict[str, str]],
    budget_context: Dict[str, Any],
) -> Tuple[Dict[str, Any], str]:
    """
    Chat multi-turno com structured output.
    conversation: [{"role": "user"|"assistant", "content": "..."}]
    budget_context: orçamento completo + metadados
    """
    if not is_openai_configured():
        raise OpenAIServiceError(
            _missing_ai_key_message(),
            status_code=503,
            code="missing_api_key",
        )

    items = budget_context.get("itens") or []
    abc_summary = _build_abc_summary(items if isinstance(items, list) else [])
    budget_json = json.dumps(budget_context, ensure_ascii=False, default=str)
    if len(budget_json) > 120_000:
        budget_json = json.dumps(
            {**budget_context, "itens": (items[:180] if isinstance(items, list) else [])},
            ensure_ascii=False,
            default=str,
        )

    system_content = REPORT_CHAT_SYSTEM_TEMPLATE.format(
        budget_json=budget_json,
        abc_summary=abc_summary,
    )

    api_messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
    for msg in conversation[-_MAX_REPORT_HISTORY:]:
        role = str(msg.get("role") or "user").lower()
        if role not in ("user", "assistant"):
            continue
        content = str(msg.get("content") or "").strip()
        if not content:
            continue
        api_messages.append({"role": role, "content": content})

    if not any(m["role"] == "user" for m in api_messages):
        raise OpenAIServiceError("Nenhuma mensagem do usuário na conversa.", status_code=400)

    client = AsyncOpenAI(api_key=get_openai_api_key(), timeout=OPENAI_ORCAMENTO_TIMEOUT_SECONDS)
    t0 = time.perf_counter()
    input_audit = {
        "messages_count": len(api_messages),
        "budget_items": len(items) if isinstance(items, list) else 0,
    }

    json_object_hint = (
        '\n\nFORMATO DE SAÍDA (JSON único, sem markdown externo): '
        '{"reply": "<markdown>", "chart": {"type": "none|bar|pie", "title": "...", '
        '"value_label": "valor|quantidade|percentual", "data": [{"name": "...", "value": 0}]}}'
    )
    attempts: List[Tuple[str, Dict[str, Any] | None]] = [
        ("json_schema", {"type": "json_schema", "json_schema": REPORT_CHAT_JSON_SCHEMA}),
        ("json_object", {"type": "json_object"}),
    ]
    last_bad_request: BadRequestError | None = None
    parsed: Dict[str, Any] | None = None
    model_used = REPORT_CHAT_MODEL

    try:
        for fmt_name, response_format in attempts:
            messages = api_messages
            if fmt_name == "json_object":
                messages = [
                    {
                        **api_messages[0],
                        "content": api_messages[0]["content"] + json_object_hint,
                    },
                    *api_messages[1:],
                ]
            try:
                response = await client.chat.completions.create(
                    model=REPORT_CHAT_MODEL,
                    temperature=0.2,
                    max_tokens=4096,
                    response_format=response_format,
                    messages=messages,
                )
            except BadRequestError as exc:
                last_bad_request = exc
                logger.warning(
                    "OpenAI report chat %s falhou (model=%s): %s",
                    fmt_name,
                    REPORT_CHAT_MODEL,
                    exc,
                )
                if fmt_name == "json_schema":
                    continue
                raise OpenAIServiceError(
                    f"OpenAI rejeitou a requisição: {exc}",
                    status_code=400,
                    code="bad_request",
                ) from exc

            raw_content = response.choices[0].message.content or "{}"
            if raw_content.startswith("```"):
                raw_content = raw_content.strip("`").removeprefix("json").strip()
            candidate = json.loads(raw_content)
            if not isinstance(candidate, dict):
                raise ValueError("Resposta não é objeto JSON")
            parsed = candidate
            break

        if parsed is None:
            raise last_bad_request or ValueError("Nenhuma resposta da OpenAI")

        duration_ms = (time.perf_counter() - t0) * 1000
        chart = _normalize_report_chart(parsed.get("chart"))
        result = {
            "reply": str(parsed.get("reply") or "").strip(),
            "chart": chart,
            "response_type": "mixed" if chart else "text",
        }

        log_ai_exchange(
            operation="generate_report_chat",
            provider="openai",
            model=model_used,
            input_payload=input_audit,
            output_payload={
                "reply_chars": len(result["reply"]),
                "has_chart": chart is not None,
            },
            duration_ms=duration_ms,
        )
        return result, f"openai:{model_used}"

    except OpenAIServiceError:
        raise
    except RateLimitError as exc:
        raise OpenAIServiceError(
            "Limite de requisições da OpenAI atingido.",
            status_code=429,
            code="rate_limit",
        ) from exc
    except (APIConnectionError, APITimeoutError) as exc:
        raise OpenAIServiceError(
            "Falha de conexão com a OpenAI.",
            status_code=503,
            code="connection_error",
        ) from exc
    except (json.JSONDecodeError, OpenAIError, ValueError) as exc:
        raise OpenAIServiceError(
            f"Resposta inválida da OpenAI: {exc}",
            status_code=502,
            code="invalid_response",
        ) from exc
