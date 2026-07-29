"""Modelos imutáveis do pipeline de orçamento (5 engines)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


LineKind = Literal["header", "title", "item", "group", "continuation", "junk", "incomplete", "unknown"]


@dataclass(frozen=True)
class TextElement:
    """Elemento de texto nativo do PDF (sem OCR)."""

    text: str
    x0: float
    y0: float  # top
    x1: float
    y1: float  # bottom
    page: int  # 1-based
    fontname: str = ""
    size: float = 0.0

    @property
    def width(self) -> float:
        return max(0.0, self.x1 - self.x0)

    @property
    def height(self) -> float:
        return max(0.0, self.y1 - self.y0)


@dataclass
class TextLine:
    """Linha reconstruída por agrupamento Y."""

    y: float
    page: int
    elements: list[TextElement] = field(default_factory=list)
    kind: LineKind = "unknown"

    @property
    def text(self) -> str:
        return " ".join(e.text for e in self.elements)


@dataclass
class RawBudgetRow:
    """Linha de planilha após atribuição a colunas (ainda não validada financeiramente)."""

    item_numero: str = ""
    codigo: str = ""
    banco: str = ""
    descricao: str = ""
    unidade: str = ""
    quantidade: float = 0.0
    valor_unitario: float = 0.0
    valor_total: float = 0.0
    peso: float = 0.0
    page: int = 0
    kind: LineKind = "unknown"
    incomplete_reason: str = ""
    cells: list[str] = field(default_factory=list)


@dataclass
class HierarchyNode:
    """Nó da árvore hierárquica (grupo ou item)."""

    item_numero: str
    codigo: str = ""
    descricao: str = ""
    unidade: str = ""
    quantidade: float = 0.0
    valor_unitario: float = 0.0
    valor_total: float = 0.0
    pagina: int = 0
    nivel: int = 0
    kind: LineKind = "item"
    filhos: list[HierarchyNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "codigo": self.codigo,
            "item_numero": self.item_numero,
            "descricao": self.descricao,
            "unidade": self.unidade,
            "quantidade": self.quantidade,
            "valorUnitario": self.valor_unitario,
            "valorTotal": self.valor_total,
            "pagina": self.pagina,
            "nivel": self.nivel,
            "kind": self.kind,
            "filhos": [f.to_dict() for f in self.filhos],
        }


@dataclass
class ValidationReport:
    ok: bool
    total_geral_documento: float | None = None
    soma_folhas: float = 0.0
    diferenca_total: float | None = None
    subtotal_mismatches: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total_geral_documento": self.total_geral_documento,
            "soma_folhas": self.soma_folhas,
            "diferenca_total": self.diferenca_total,
            "subtotal_mismatches": self.subtotal_mismatches,
            "errors": self.errors,
            "warnings": self.warnings,
        }


@dataclass
class PipelineResult:
    """Saída canônica do pipeline (engines 1–5)."""

    items: list[dict[str, Any]] = field(default_factory=list)
    hierarchy: list[dict[str, Any]] = field(default_factory=list)
    incomplete: list[dict[str, Any]] = field(default_factory=list)
    abc_summary: dict[str, Any] = field(default_factory=dict)
    validation: ValidationReport = field(default_factory=lambda: ValidationReport(ok=False))
    engine_logs: list[dict[str, Any]] = field(default_factory=list)
    document_total: float | None = None
    pages_processed: list[int] = field(default_factory=list)
    document_kind: str = "planilha_orcamentaria"
    estimativo_meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": self.items,
            "hierarchy": self.hierarchy,
            "incomplete": self.incomplete,
            "abc_summary": self.abc_summary,
            "validation": self.validation.to_dict(),
            "engine_logs": self.engine_logs,
            "document_total": self.document_total,
            "pages_processed": self.pages_processed,
            "document_kind": self.document_kind,
            "estimativo_meta": self.estimativo_meta,
        }
