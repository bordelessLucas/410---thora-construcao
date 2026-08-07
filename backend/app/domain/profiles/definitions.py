"""Perfis concretos de documentos orçamentários."""

from __future__ import annotations

from app.domain.profiles.adapters import (
    parse_bdi_coluna,
    parse_curva_abc,
    parse_generico,
    parse_novacap_planilha,
    parse_novacap_sintetico,
)
from app.domain.profiles.base import DocumentProfile


NOVACAP_SINTETICO = DocumentProfile(
    id="novacap_sintetico",
    name="NOVACAP Orçamento Sintético",
    table_kind="sintetico",
    economic_source="total_com_bdi",
    preferred_for_abc=True,
    skip_kinds_when_selected=("composicao", "analitico"),
    prefer_leaf_only=True,
    detect_hints=(
        "orçamento sintético",
        "orcamento sintetico",
        "sintético",
        "sintetico",
        "peso %",
        "novacap",
    ),
    header_tokens=("item", "descri", "unidade", "quant", "valor unit", "total", "peso"),
    ia_layout_hint=(
        "Orçamento Sintético NOVACAP: Item | Descrição | Unidade | Quantidade | "
        "Valor Unit. | Total | Peso %. Use o Total da linha (já com BDI). "
        "Continuações de página mantêm a mesma estrutura sem título."
    ),
    parse_rows=parse_novacap_sintetico,
)

NOVACAP_PLANILHA = DocumentProfile(
    id="novacap_planilha",
    name="NOVACAP Planilha Orçamentária",
    table_kind="orcamento",
    economic_source="total_com_bdi",
    preferred_for_abc=True,
    skip_kinds_when_selected=("composicao",),
    prefer_leaf_only=True,
    detect_hints=(
        "planilha orçamentária",
        "planilha orcamentaria",
        "total c/ bdi",
        "total c/bdi",
        "fonte",
        "banco",
    ),
    header_tokens=("item", "código", "codigo", "descri", "quant", "bdi", "total"),
    ia_layout_hint=(
        "Planilha Item|Fonte|Código|Descrição|Unidade|Quantidade|VU s/BDI|BDI%|Total c/BDI. "
        "Prefira Total c/BDI como valor econômico da linha."
    ),
    parse_rows=parse_novacap_planilha,
)

BDI_COLUNA = DocumentProfile(
    id="bdi_coluna",
    name="Planilha com coluna BDI %",
    table_kind="orcamento",
    economic_source="total_com_bdi",
    preferred_for_abc=True,
    prefer_leaf_only=True,
    detect_hints=("bdi %", "% bdi", "bdi (%)", "incidência bdi"),
    header_tokens=("item", "descri", "quant", "bdi", "valor", "total"),
    ia_layout_hint=(
        "Planilha com coluna BDI % explícita. Não confunda 'Valor Unit com BDI' "
        "com percentual de BDI."
    ),
    parse_rows=parse_bdi_coluna,
)

COMPOSICAO = DocumentProfile(
    id="composicao_unitaria",
    name="Composição unitária / insumos",
    table_kind="composicao",
    economic_source="total",
    preferred_for_abc=False,
    prefer_leaf_only=False,
    detect_hints=(
        "composição de custos",
        "composicao de custos",
        "composição unitária",
        "composicao unitaria",
        "insumo",
        "coeficiente",
    ),
    header_tokens=("código", "codigo", "descri", "unidade", "coef", "preço", "preco"),
    ia_layout_hint="Composição de insumos — não usar como fonte da Curva ABC do edital.",
    parse_rows=parse_generico,
)

ANALITICO = DocumentProfile(
    id="orcamento_analitico",
    name="Orçamento Analítico",
    table_kind="analitico",
    economic_source="total",
    preferred_for_abc=False,
    skip_kinds_when_selected=("composicao",),
    prefer_leaf_only=True,
    detect_hints=(
        "orçamento analítico",
        "orcamento analitico",
        "planilha orçamentária analítica",
        "planilha orcamentaria analitica",
        "analítica",
        "analitica",
    ),
    header_tokens=("item", "descri", "quant", "valor", "total"),
    ia_layout_hint="Orçamento analítico / detalhe — secundário à Curva ABC se houver sintético.",
    parse_rows=parse_generico,
)

GENERICO = DocumentProfile(
    id="generico_keywords",
    name="Genérico (keywords)",
    table_kind="orcamento",
    economic_source="total_com_bdi",
    preferred_for_abc=False,
    prefer_leaf_only=True,
    detect_hints=("orçamento", "orcamento", "planilha", "serviço", "servico"),
    header_tokens=("item", "descri", "quant", "valor", "total"),
    ia_layout_hint="Planilha orçamentária genérica. Prefira total com BDI quando existir.",
    parse_rows=parse_generico,
)

CURVA_ABC = DocumentProfile(
    id="curva_abc",
    name="Curva ABC de Serviços (pronta)",
    table_kind="orcamento",
    economic_source="total",
    preferred_for_abc=True,
    prefer_leaf_only=True,
    detect_hints=(
        "curva abc",
        "curva abc de serviços",
        "curva abc de servicos",
        "custo parcial",
        "% incid",
        "% acumul",
        "faixa",
    ),
    header_tokens=(
        "código",
        "codigo",
        "descri",
        "quant",
        "custo unit",
        "custo parcial",
        "incid",
        "acumul",
        "faixa",
    ),
    ia_layout_hint=(
        "Curva ABC pronta: Código|Descrição|Unid|Quantidade|Custo Unit|Custo Parcial|"
        "% Incid|% Acumul|Faixa. Use Custo Parcial como valor total da linha."
    ),
    parse_rows=parse_curva_abc,
    metadata={"document_form": "abc_ready"},
)

ALL_PROFILES: tuple[DocumentProfile, ...] = (
    CURVA_ABC,
    NOVACAP_SINTETICO,
    NOVACAP_PLANILHA,
    BDI_COLUNA,
    COMPOSICAO,
    ANALITICO,
    GENERICO,
)
