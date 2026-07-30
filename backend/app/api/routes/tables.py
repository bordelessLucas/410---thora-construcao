import asyncio
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from app.api.deps import get_current_user_id
from app.config import (
    DETECT_TABLES_MAX_PAGES,
    DETECT_TABLES_SKIP_THUMBNAILS,
    MAX_FILE_SIZE,
)
from app.domain.schemas.table import TableDetectResponse
from app.domain.services.table_detection import (
    detect_table_options,
    public_options_from_raw,
    recommended_table_ids,
)
from app.infrastructure.pdf.table_extract import count_pdf_pages
from app.infrastructure.storage.detect_job_store import DetectJobStore
from app.infrastructure.storage.table_cache_store import TableCacheStore
from app.infrastructure.storage.upload_store import UploadStore

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/orcamentos", tags=["tables"])

_upload_store = UploadStore()
_table_cache = TableCacheStore()
_detect_jobs = DetectJobStore()
_running_tasks: dict[str, asyncio.Task[Any]] = {}


def _success_response(
    upload_id: str,
    options: list[dict[str, Any]],
    *,
    cached: bool = False,
    mock_fallback: bool = False,
    message: str | None = None,
    pages_total: int = 0,
    pages_done: int = 0,
) -> TableDetectResponse:
    public = public_options_from_raw(options)
    return TableDetectResponse(
        status="success",
        upload_id=upload_id,
        tables_found=len(public),
        options=public,
        mock_fallback=mock_fallback,
        cached=cached,
        recommended_table_ids=recommended_table_ids(options),
        pages_total=pages_total,
        pages_done=pages_done or pages_total,
        candidates_found=len(public),
        message=message or f"{len(public)} tabela(s) detectada(s)",
    )


def _job_to_response(job: dict[str, Any]) -> TableDetectResponse:
    status = str(job.get("status") or "processing")
    result = job.get("result")
    if status == "completed" and isinstance(result, dict):
        options = list(result.get("options") or [])
        # Result truncado no Firestore → hidratar do cache (Storage/disco).
        if not options and (
            result.get("options_in_cache") or int(result.get("tables_found") or 0) > 0
        ):
            cached_opts, _ = _table_cache.get(str(job.get("upload_id") or ""))
            if cached_opts:
                options = public_options_from_raw(cached_opts)
        recommended = list(result.get("recommended_table_ids") or [])
        if not recommended and options:
            recommended = recommended_table_ids(options)
        return TableDetectResponse(
            status="success",
            upload_id=str(job.get("upload_id")),
            tables_found=int(result.get("tables_found") or len(options) or 0),
            options=options,
            mock_fallback=bool(result.get("mock_fallback")),
            cached=bool(result.get("cached")),
            recommended_table_ids=recommended,
            pages_total=int(job.get("pages_total") or 0),
            pages_done=int(job.get("pages_done") or job.get("pages_total") or 0),
            candidates_found=int(
                job.get("candidates_found") or result.get("tables_found") or len(options) or 0
            ),
            message=str(job.get("message") or "Detecção concluída"),
        )
    return TableDetectResponse(
        status=status if status in {"processing", "queued", "failed"} else "processing",
        upload_id=str(job.get("upload_id")),
        tables_found=0,
        options=[],
        pages_total=int(job.get("pages_total") or 0),
        pages_done=int(job.get("pages_done") or 0),
        candidates_found=int(job.get("candidates_found") or 0),
        message=str(job.get("message") or ""),
        error=str(job.get("error")) if job.get("error") else None,
    )


def _run_detect_sync(upload_id: str, file_path: Path) -> tuple[list[dict[str, Any]], bool, int]:
    """Mesmo algoritmo do local (PDF aberto uma vez), com heartbeats thread-safe."""
    pages_total = count_pdf_pages(file_path, DETECT_TABLES_MAX_PAGES)
    _detect_jobs.heartbeat(
        upload_id,
        pages_done=0,
        pages_total=pages_total,
        candidates_found=0,
        message=f"Iniciando análise de {pages_total} página(s)…",
    )

    def on_progress(update: dict[str, Any]) -> None:
        done = int(update.get("pages_done") or 0)
        total = int(update.get("pages_total") or pages_total)
        raw = int(update.get("raw_tables") or 0)
        _detect_jobs.heartbeat(
            upload_id,
            pages_done=done,
            pages_total=total,
            candidates_found=raw,
            message=str(update.get("message") or f"Página {done}/{total}"),
        )

    options, mock_fallback = detect_table_options(
        file_path,
        on_progress=on_progress,
        max_pages=DETECT_TABLES_MAX_PAGES,
        min_nonempty_rows=8,
    )
    return options, mock_fallback, pages_total


async def _run_detect_job(upload_id: str, file_path: Path, user_id: str) -> None:
    """Roda a detecção em thread (igual ao local) sem bloquear /health e status."""
    t0 = time.perf_counter()
    logger.info(
        "[detect-job] RUN start upload=%s skip_thumbs=%s max_pages=%s path=%s size=%.2fMB user=%s",
        upload_id,
        DETECT_TABLES_SKIP_THUMBNAILS,
        DETECT_TABLES_MAX_PAGES,
        file_path.name,
        file_path.stat().st_size / 1024 / 1024 if file_path.is_file() else 0,
        user_id[:12] if user_id else "-",
    )
    try:
        options, mock_fallback, pages_total = await asyncio.to_thread(
            _run_detect_sync,
            upload_id,
            file_path,
        )

        if not options:
            logger.warning(
                "[detect-job] upload=%s 0 tabelas na 1ª passagem — retry min_rows=3",
                upload_id,
            )

            def _retry() -> tuple[list[dict[str, Any]], bool, int]:
                total = count_pdf_pages(file_path, DETECT_TABLES_MAX_PAGES)

                def on_progress(update: dict[str, Any]) -> None:
                    _detect_jobs.heartbeat(
                        upload_id,
                        pages_done=int(update.get("pages_done") or 0),
                        pages_total=int(update.get("pages_total") or total),
                        candidates_found=int(update.get("raw_tables") or 0),
                        message=str(update.get("message") or "Modo permissivo…"),
                    )

                opts, fb = detect_table_options(
                    file_path,
                    on_progress=on_progress,
                    max_pages=DETECT_TABLES_MAX_PAGES,
                    min_nonempty_rows=3,
                )
                return opts, fb, total

            options, mock_fallback, pages_total = await asyncio.to_thread(_retry)

        _table_cache.save(upload_id, options)
        public = public_options_from_raw(options)
        result_payload = {
            "tables_found": len(public),
            "options": public,
            "mock_fallback": mock_fallback,
            "cached": False,
            "recommended_table_ids": recommended_table_ids(options),
        }
        msg = (
            f"Concluído: {len(public)} tabela(s) em {time.perf_counter() - t0:.1f}s"
            if public
            else (
                f"Nenhuma tabela válida em {pages_total} página(s) "
                f"(limite={DETECT_TABLES_MAX_PAGES}). Tente outro PDF ou aumente DETECT_TABLES_MAX_PAGES."
            )
        )
        _detect_jobs.update(
            upload_id,
            status="completed",
            pages_done=pages_total,
            pages_total=pages_total,
            candidates_found=len(public),
            message=msg,
            error=None if public else msg,
            result=result_payload,
        )
        logger.info(
            "[detect-job] DONE upload=%s tables=%s elapsed=%.2fs",
            upload_id,
            len(public),
            time.perf_counter() - t0,
        )
    except Exception as exc:
        logger.exception(
            "[detect-job] FAILED upload=%s after %.2fs: %s",
            upload_id,
            time.perf_counter() - t0,
            exc,
        )
        _detect_jobs.update(
            upload_id,
            status="failed",
            error=str(exc),
            message="Falha na detecção de tabelas",
        )
    finally:
        _running_tasks.pop(upload_id, None)


@router.post("/detect-tables", response_model=TableDetectResponse)
async def detect_orcamento_tables(
    upload_id: str = Form(...),
    user_id: str = Depends(get_current_user_id),
    file: UploadFile | None = File(None),
    sync: str | None = Form(None),
    force: str | None = Form(None),
):
    """
    Inicia detecção assíncrona (mesmo algoritmo do local) ou retorna cache.

    - Com `file` ou `force=true`: invalida cache/job anterior e reprocessa
    - Poll: GET `/api/orcamentos/detect-tables/status/{upload_id}`
    """
    t0 = time.perf_counter()
    upload_id = UploadStore.validate_upload_id(upload_id)
    force_sync = str(sync or "").lower() in {"1", "true", "yes", "on"}
    force_reprocess = str(force or "").lower() in {"1", "true", "yes", "on"} or file is not None
    logger.info(
        "[detect-tables] INÍCIO upload_id=%s user=%s file=%s sync=%s force=%s skip_thumbs=%s max_pages=%s",
        upload_id,
        user_id[:12] if user_id else "-",
        file.filename if file else None,
        force_sync,
        force_reprocess,
        DETECT_TABLES_SKIP_THUMBNAILS,
        DETECT_TABLES_MAX_PAGES,
    )

    if file is not None:
        if file.content_type and file.content_type != "application/pdf":
            raise HTTPException(status_code=400, detail="Apenas arquivos PDF são permitidos")
        contents = await file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="Arquivo PDF vazio")
        if len(contents) > MAX_FILE_SIZE:
            max_mb = MAX_FILE_SIZE / 1024 / 1024
            raise HTTPException(
                status_code=413,
                detail=f"Arquivo muito grande. Máximo: {max_mb:.0f}MB.",
            )
        filename = file.filename or f"{upload_id}.pdf"
        _upload_store.save_pdf(
            upload_id,
            contents,
            user_id=user_id,
            filename=filename,
            content_type=file.content_type or "application/pdf",
        )
        logger.info(
            "[detect-tables] PDF salvo (%s, %.2f MB) em %.2fs",
            upload_id,
            len(contents) / 1024 / 1024,
            time.perf_counter() - t0,
        )
    else:
        _upload_store.assert_access(upload_id, user_id)

    if force_reprocess:
        logger.info("[detect-tables] invalidando cache/job anterior upload=%s", upload_id)
        _table_cache.clear(upload_id)
        _detect_jobs.clear(upload_id)
        old_task = _running_tasks.pop(upload_id, None)
        if old_task and not old_task.done():
            old_task.cancel()

    file_path = _upload_store.ensure_pdf(upload_id, user_id=user_id)
    meta = _upload_store.load_meta(upload_id)
    filename = str(meta.get("filename") or file_path.name)

    if not force_reprocess and _table_cache.is_valid(upload_id):
        options, _ = _table_cache.get(upload_id)
        logger.info(
            "[detect-tables] CACHE HIT → %s tabela(s) em %.2fs",
            len(options),
            time.perf_counter() - t0,
        )
        return _success_response(upload_id, options, cached=True, message="Cache de tabelas")

    existing = _detect_jobs.get(upload_id)
    if existing and existing.get("status") in {"processing", "queued"}:
        logger.info("[detect-tables] job já em andamento upload=%s", upload_id)
        return _job_to_response(existing)
    if (
        not force_reprocess
        and existing
        and existing.get("status") == "completed"
        and isinstance(existing.get("result"), dict)
        and int((existing.get("result") or {}).get("tables_found") or 0) > 0
    ):
        logger.info("[detect-tables] job completed (com tabelas) upload=%s", upload_id)
        return _job_to_response(existing)

    if force_sync:
        logger.info("[detect-tables] modo SYNC (debug)")
        try:
            def _sync_detect() -> tuple[list[dict[str, Any]], bool]:
                return detect_table_options(
                    file_path,
                    max_pages=DETECT_TABLES_MAX_PAGES,
                    min_nonempty_rows=8,
                )

            options, fallback_used = await asyncio.to_thread(_sync_detect)
        except Exception as exc:
            logger.exception("[detect-tables] sync falhou: %s", exc)
            raise HTTPException(status_code=500, detail=f"Erro ao analisar PDF: {exc}") from exc
        _table_cache.save(upload_id, options)
        return _success_response(upload_id, options, mock_fallback=fallback_used)

    pages_total = await asyncio.to_thread(count_pdf_pages, file_path, DETECT_TABLES_MAX_PAGES)
    _detect_jobs.init_job(
        upload_id,
        user_id=user_id,
        filename=filename,
        pages_total=pages_total,
        message=f"Na fila — {pages_total} página(s) a analisar",
    )

    if upload_id in _running_tasks and not _running_tasks[upload_id].done():
        logger.warning("[detect-tables] task duplicada ignorada upload=%s", upload_id)
    else:
        task = asyncio.create_task(_run_detect_job(upload_id, file_path, user_id))
        _running_tasks[upload_id] = task
        logger.info(
            "[detect-tables] job enfileirado upload=%s pages=%s (%.2fs até enqueue)",
            upload_id,
            pages_total,
            time.perf_counter() - t0,
        )

    job = _detect_jobs.get(upload_id) or {}
    return _job_to_response(job)


@router.get("/detect-tables/status/{upload_id}", response_model=TableDetectResponse)
async def detect_tables_status(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    """Acompanha progresso da detecção assíncrona."""
    upload_id = UploadStore.validate_upload_id(upload_id)
    _upload_store.assert_access(upload_id, user_id)

    job = _detect_jobs.get(upload_id)
    if job:
        logger.info(
            "[detect-status] upload=%s status=%s pages=%s/%s candidatas=%s msg=%s",
            upload_id,
            job.get("status"),
            job.get("pages_done"),
            job.get("pages_total"),
            job.get("candidates_found"),
            str(job.get("message") or "")[:80],
        )
        return _job_to_response(job)

    if _table_cache.is_valid(upload_id):
        options, _ = _table_cache.get(upload_id)
        return _success_response(upload_id, options, cached=True, message="Cache de tabelas")

    raise HTTPException(
        status_code=404,
        detail="Nenhuma detecção em andamento para este upload. Envie o PDF novamente.",
    )


@router.get("/{upload_id}/table-candidates", response_model=TableDetectResponse)
async def get_table_candidates(
    upload_id: str,
    user_id: str = Depends(get_current_user_id),
):
    upload_id = UploadStore.validate_upload_id(upload_id)
    _upload_store.assert_access(upload_id, user_id)

    options, _ = _table_cache.get(upload_id)
    public = public_options_from_raw(options)
    logger.info(
        "[table-candidates] upload_id=%s tables=%s",
        upload_id,
        len(public),
    )
    return TableDetectResponse(
        upload_id=upload_id,
        tables_found=len(public),
        options=public,
        cached=True,
        recommended_table_ids=recommended_table_ids(options),
        candidates_found=len(public),
        message=f"{len(public)} tabela(s) em cache",
    )
