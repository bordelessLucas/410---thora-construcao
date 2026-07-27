from typing import Any, Literal

from pydantic import BaseModel, Field

AnalysisTypeId = Literal["curva_abc"]


class ProcessTablesRequest(BaseModel):
    upload_id: str
    table_ids: list[str] = Field(min_length=1)
    analysis_types: list[AnalysisTypeId] = Field(default_factory=lambda: ["curva_abc"])


class ProcessTablesResponse(BaseModel):
    status: str = "success"
    upload_id: str
    filename: str
    tables_found: int
    items_found: int
    analysis_types: list[str]
    engine: str = "openai_hybrid"
    tables: list[dict[str, Any]] = Field(default_factory=list)
    items: list[dict[str, Any]] = Field(default_factory=list)
    structured_items: list[dict[str, Any]] = Field(default_factory=list)
    hierarchical_items: list[dict[str, Any]] = Field(default_factory=list)
    resumo: dict[str, Any] = Field(default_factory=dict)
    abc_summary: dict[str, Any] = Field(default_factory=dict)
    ia_metadata: dict[str, Any] = Field(default_factory=dict)
    message: str = ""
