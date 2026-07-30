from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import CACHE_DIR, DETECT_TABLES_CACHE_VERSION, IS_CLOUD

logger = logging.getLogger(__name__)


def _storage_blob_path(upload_id: str) -> str:
    return f"table_caches/{upload_id}_tables.json"


class TableCacheStore:
    """Cache de candidatos de tabela: disco local + Firebase Storage (Cloud Run)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or CACHE_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, upload_id: str) -> Path:
        return self._base_dir / f"{upload_id}_tables.json"

    def _load_disk(self, upload_id: str) -> dict[str, Any] | None:
        path = self._path(upload_id)
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[table-cache] falha leitura disco %s: %s", upload_id, exc)
            return None

    def _save_disk(self, upload_id: str, payload: dict[str, Any]) -> None:
        self._path(upload_id).write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

    def _load_storage(self, upload_id: str) -> dict[str, Any] | None:
        if not IS_CLOUD:
            return None
        try:
            from services.storage_service import download_bytes

            raw = download_bytes(_storage_blob_path(upload_id))
            if not raw:
                return None
            data = json.loads(raw.decode("utf-8"))
            return data if isinstance(data, dict) else None
        except Exception as exc:
            logger.warning("[table-cache] falha Storage %s: %s", upload_id, exc)
            return None

    def _save_storage(self, upload_id: str, payload: dict[str, Any]) -> None:
        if not IS_CLOUD:
            return
        try:
            from services.storage_service import upload_bytes

            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            ok = upload_bytes(
                _storage_blob_path(upload_id),
                raw,
                content_type="application/json",
            )
            if not ok:
                logger.warning("[table-cache] Storage não confirmou save %s", upload_id)
        except Exception as exc:
            logger.warning("[table-cache] falha ao salvar Storage %s: %s", upload_id, exc)

    def _delete_storage(self, upload_id: str) -> None:
        if not IS_CLOUD:
            return
        try:
            from services.storage_service import delete_blob

            delete_blob(_storage_blob_path(upload_id))
        except Exception as exc:
            logger.debug("[table-cache] delete Storage %s: %s", upload_id, exc)

    def get(self, upload_id: str) -> tuple[list[dict[str, Any]], int]:
        data = self._load_disk(upload_id)
        if data is None:
            data = self._load_storage(upload_id)
            if data is not None:
                try:
                    self._save_disk(upload_id, data)
                except OSError:
                    pass
        if not data:
            return [], 0
        return data.get("options") or [], int(data.get("version") or 0)

    def save(self, upload_id: str, options: list[dict[str, Any]]) -> None:
        payload = {
            "upload_id": upload_id,
            "version": DETECT_TABLES_CACHE_VERSION,
            "options": options,
        }
        self._save_disk(upload_id, payload)
        self._save_storage(upload_id, payload)

    def clear(self, upload_id: str) -> None:
        path = self._path(upload_id)
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
        self._delete_storage(upload_id)

    def is_valid(self, upload_id: str) -> bool:
        options, version = self.get(upload_id)
        return bool(options) and version >= DETECT_TABLES_CACHE_VERSION
