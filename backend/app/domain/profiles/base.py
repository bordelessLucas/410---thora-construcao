"""
Contrato de perfil de documento orçamentário.

Cada perfil descreve como reconhecer um layout típico de edital/planilha
brasileira e qual estratégia usar para extrair linhas econômicas.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Literal

TableKind = Literal["sintetico", "analitico", "composicao", "orcamento"]
EconomicSource = Literal["total_com_bdi", "total", "vu_com_bdi"]

RowAdapter = Callable[[list[list[Any]], int], list[dict[str, Any]]]


@dataclass(frozen=True)
class ProfileMatch:
    profile_id: str
    confidence: float
    table_kind: TableKind
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentProfile:
    """Perfil determinístico de layout de planilha orçamentária."""

    id: str
    name: str
    table_kind: TableKind
    economic_source: EconomicSource = "total_com_bdi"
    # Preferir esta tabela na Curva ABC quando presente na seleção
    preferred_for_abc: bool = False
    # Tipos a ignorar na ABC quando este perfil vence na seleção
    skip_kinds_when_selected: tuple[TableKind, ...] = ()
    # Só folhas hierárquicas (evita grupo+filho)
    prefer_leaf_only: bool = True
    # Strings que aumentam confiança (nome/preview/header)
    detect_hints: tuple[str, ...] = ()
    # Tokens de cabeçalho esperados (pelo menos alguns)
    header_tokens: tuple[str, ...] = ()
    # Prompt curto para condicionar a IA ao layout
    ia_layout_hint: str = ""
    # Adapter opcional; se None, usa BudgetParser.parse_table genérico
    parse_rows: RowAdapter | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def score_rows(
        self,
        rows: list[list[Any]],
        *,
        table_name: str = "",
    ) -> ProfileMatch:
        """Pontua o quanto este perfil combina com a matriz de células."""
        from app.domain.profiles.scoring import score_profile_against_rows

        return score_profile_against_rows(self, rows, table_name=table_name)
