import logging
import time

from fastapi import APIRouter, Depends

from app.api.deps import get_current_user_id
from app.domain.schemas.process import ProcessTablesRequest, ProcessTablesResponse
from app.domain.services.orcamento_extraction import process_selected_tables

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orcamentos", tags=["process"])


@router.post("/process-tables", response_model=ProcessTablesResponse)
async def process_orcamento_tables(
    payload: ProcessTablesRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Extrai via pipeline 5 engines; OpenAI só insights opcionais sobre JSON."""
    t0 = time.perf_counter()
    logger.info(
        "[process-tables] INÍCIO upload=%s tables=%s tipos=%s user=%s",
        payload.upload_id,
        payload.table_ids,
        payload.analysis_types,
        user_id[:12] if user_id else "-",
    )
    result = await process_selected_tables(
        payload.upload_id,
        user_id,
        payload.table_ids,
        list(payload.analysis_types),
    )
    logger.info(
        "[process-tables] FIM upload=%s itens=%s engine=%s em %.2fs",
        payload.upload_id,
        result.get("items_found"),
        result.get("engine"),
        time.perf_counter() - t0,
    )
    return ProcessTablesResponse(**result)
