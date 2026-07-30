from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DETECT_JOB_STALE_SECONDS, IS_CLOUD, JOBS_DIR

logger = logging.getLogger(__name__)

FIRESTORE_COLLECTION = "detect_jobs"
# Firestore doc limit ~1 MiB — result com muitas preview_rows pode estourar.
_MAX_RESULT_JSON_BYTES = 700_000


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _firestore_db():
    if not IS_CLOUD:
        return None
    try:
        from firebase_service import db as firestore_db

        return firestore_db
    except Exception as exc:
        logger.debug("[detect-job] Firestore indisponível: %s", exc)
        return None


def _json_safe(value: Any) -> Any:
    """Garante tipos aceitos pelo Firestore (via round-trip JSON)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _cloud_payload(job: dict[str, Any]) -> dict[str, Any]:
    payload = _json_safe(job)
    result = payload.get("result")
    if not isinstance(result, dict):
        return payload
    raw = json.dumps(result, ensure_ascii=False)
    if len(raw.encode("utf-8")) <= _MAX_RESULT_JSON_BYTES:
        return payload
    # Mantém metadados; opções completas ficam no TableCacheStore (Storage).
    payload["result"] = {
        "tables_found": int(result.get("tables_found") or 0),
        "mock_fallback": bool(result.get("mock_fallback")),
        "cached": bool(result.get("cached")),
        "recommended_table_ids": list(result.get("recommended_table_ids") or []),
        "options": [],
        "options_in_cache": True,
    }
    return payload


class DetectJobStore:
    """Status de detecção: memória + disco; em Cloud Run também Firestore (multi-instância)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or JOBS_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _path(self, upload_id: str) -> Path:
        return self._base_dir / f"{upload_id}_detect_job.json"

    def _persist_disk(self, job: dict[str, Any]) -> None:
        upload_id = str(job.get("upload_id") or "")
        if not upload_id:
            return
        try:
            self._path(upload_id).write_text(
                json.dumps(job, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning("[detect-job] falha ao persistir disco %s: %s", upload_id, exc)

    def _persist_firestore(self, job: dict[str, Any]) -> None:
        upload_id = str(job.get("upload_id") or "")
        if not upload_id:
            return
        db = _firestore_db()
        if not db:
            return
        try:
            db.collection(FIRESTORE_COLLECTION).document(upload_id).set(
                _cloud_payload(job),
                merge=True,
            )
        except Exception as exc:
            logger.warning("[detect-job] falha Firestore %s: %s", upload_id, exc)

    def _persist(self, job: dict[str, Any]) -> None:
        self._persist_disk(job)
        self._persist_firestore(job)

    def _load_disk(self, upload_id: str) -> dict[str, Any] | None:
        path = self._path(upload_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[detect-job] falha ao ler disco %s: %s", upload_id, exc)
        return None

    def _load_firestore(self, upload_id: str) -> dict[str, Any] | None:
        db = _firestore_db()
        if not db:
            return None
        try:
            snap = db.collection(FIRESTORE_COLLECTION).document(upload_id).get()
            if not snap.exists:
                return None
            data = snap.to_dict()
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("[detect-job] falha ao ler Firestore %s: %s", upload_id, exc)
            return None

    def init_job(
        self,
        upload_id: str,
        *,
        user_id: str,
        filename: str | None = None,
        pages_total: int = 0,
        message: str = "Na fila…",
    ) -> dict[str, Any]:
        now = _utcnow()
        job = {
            "upload_id": upload_id,
            "user_id": user_id,
            "filename": filename,
            "status": "processing",
            "pages_total": pages_total,
            "pages_done": 0,
            "candidates_found": 0,
            "message": message,
            "error": None,
            "result": None,
            "created_at": now,
            "updated_at": now,
        }
        with self._lock:
            self._jobs[upload_id] = job
            self._persist(job)
        logger.info("[detect-job] init upload=%s pages_total=%s", upload_id, pages_total)
        return dict(job)

    def get(self, upload_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(upload_id)
            if job is None:
                job = self._load_disk(upload_id)
            if job is None:
                job = self._load_firestore(upload_id)
            if job:
                self._jobs[upload_id] = job
                self._persist_disk(job)
            if not job:
                return None
            job = self._mark_stale_if_needed(job)
            return dict(job)

    def _mark_stale_if_needed(self, job: dict[str, Any]) -> dict[str, Any]:
        if job.get("status") not in {"processing", "queued"}:
            return job
        updated = _parse_iso(str(job.get("updated_at") or ""))
        if not updated:
            return job
        age = (datetime.now(timezone.utc) - updated).total_seconds()
        if age < DETECT_JOB_STALE_SECONDS:
            return job
        job["status"] = "failed"
        job["error"] = (
            f"Detecção interrompida (sem progresso há {int(age)}s). "
            "O worker pode ter reiniciado ou esgotado memória — tente novamente."
        )
        job["message"] = "Falhou por timeout/inatividade"
        job["updated_at"] = _utcnow()
        self._persist(job)
        logger.error(
            "[detect-job] STALE upload=%s age=%.0fs → failed",
            job.get("upload_id"),
            age,
        )
        return job

    def update(self, upload_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(upload_id) or self._load_disk(upload_id) or self._load_firestore(
                upload_id
            )
            if not job:
                return None
            job.update(fields)
            job["updated_at"] = _utcnow()
            self._jobs[upload_id] = job
            self._persist(job)
            return dict(job)

    def clear(self, upload_id: str) -> None:
        with self._lock:
            self._jobs.pop(upload_id, None)
            path = self._path(upload_id)
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        db = _firestore_db()
        if db:
            try:
                db.collection(FIRESTORE_COLLECTION).document(upload_id).delete()
            except Exception as exc:
                logger.warning("[detect-job] falha ao limpar Firestore %s: %s", upload_id, exc)
        logger.info("[detect-job] cleared upload=%s", upload_id)

    def heartbeat(
        self,
        upload_id: str,
        *,
        pages_done: int,
        pages_total: int,
        candidates_found: int,
        message: str,
    ) -> None:
        self.update(
            upload_id,
            status="processing",
            pages_done=pages_done,
            pages_total=pages_total,
            candidates_found=candidates_found,
            message=message,
            error=None,
        )
