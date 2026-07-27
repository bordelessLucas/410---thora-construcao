"""Endpoints da Curva ABC canônica."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import get_current_user_id
from app.domain.abc_curve import build_abc_summary, classify_abc_items
from app.infrastructure.storage.upload_store import UploadStore

router = APIRouter(tags=["curva-abc"])


class CurvaAbcClassifyRequest(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)
    upload_id: str | None = None


@router.post("/api/curva-abc/classify")
async def classify_curva_abc(
    payload: CurvaAbcClassifyRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Recalcula a Curva ABC (Pareto 80/95) a partir dos itens enviados."""
    if payload.upload_id:
        upload_id = UploadStore.validate_upload_id(payload.upload_id)
        UploadStore().assert_access(upload_id, user_id)

    if not payload.items:
        raise HTTPException(status_code=400, detail="Informe a lista de itens.")

    classified = classify_abc_items(payload.items)
    summary = build_abc_summary(classified)
    return {
        "status": "success",
        "items": classified,
        "summary": summary,
        "abc_summary": summary,
    }


@router.get("/api/curva-abc/{upload_id}")
async def get_curva_abc_from_upload(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """
    Compatibilidade: tenta montar ABC a partir do meta/cache do upload.
    Preferir POST /api/curva-abc/classify com os itens da validação.
    """
    upload_id = UploadStore.validate_upload_id(upload_id)
    store = UploadStore()
    store.assert_access(upload_id, user_id)
    meta = store.load_meta(upload_id)

    items = meta.get("items") or meta.get("structured_items") or []
    if not isinstance(items, list) or not items:
        raise HTTPException(
            status_code=404,
            detail=(
                "Itens do orçamento não persistidos neste upload. "
                "Use POST /api/curva-abc/classify com os itens da validação."
            ),
        )

    classified = classify_abc_items([i for i in items if isinstance(i, dict)])
    summary = build_abc_summary(classified)
    return {
        "status": "success",
        "upload_id": upload_id,
        "items": classified,
        "summary": summary,
        "abc_summary": summary,
    }
