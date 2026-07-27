from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from app.config import IS_CLOUD, UPLOAD_DIR

logger = logging.getLogger(__name__)


class UploadStore:
    """Persistência local de PDFs + backup no Firebase Storage (cloud)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base_dir = base_dir or UPLOAD_DIR
        self._base_dir.mkdir(parents=True, exist_ok=True)

    def _pdf_path(self, upload_id: str) -> Path:
        return self._base_dir / f"{upload_id}.pdf"

    def _meta_path(self, upload_id: str) -> Path:
        return self._base_dir / f".meta_{upload_id}.json"

    @staticmethod
    def validate_upload_id(upload_id: str) -> str:
        try:
            uuid.UUID(upload_id)
            return upload_id
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="upload_id inválido") from exc

    def save_pdf(
        self,
        upload_id: str,
        pdf_bytes: bytes,
        *,
        user_id: str,
        filename: str,
        content_type: str,
    ) -> None:
        self._pdf_path(upload_id).write_bytes(pdf_bytes)
        meta: dict[str, Any] = {
            "uploadId": upload_id,
            "userId": user_id,
            "filename": filename,
            "content_type": content_type,
            "cloudUploadStatus": "pending",
        }
        self._write_meta(upload_id, meta)

        # Em cloud o disco é efêmero — persistir no Storage de imediato.
        cloud_url = self._upload_to_storage(
            upload_id=upload_id,
            user_id=user_id,
            pdf_bytes=pdf_bytes,
            content_type=content_type,
        )
        if cloud_url:
            meta["cloudUploadStatus"] = "completed"
            meta["cloudUrl"] = cloud_url
            self._write_meta(upload_id, meta)
        elif IS_CLOUD:
            meta["cloudUploadStatus"] = "failed"
            self._write_meta(upload_id, meta)
            logger.error(
                "PDF NÃO persistido no Storage (upload=%s). Disco efêmero pode perder o arquivo.",
                upload_id,
            )

    @staticmethod
    def _upload_to_storage(
        *,
        upload_id: str,
        user_id: str,
        pdf_bytes: bytes,
        content_type: str,
    ) -> str | None:
        try:
            from services.storage_service import upload_pdf_bytes
        except Exception as exc:
            logger.warning("storage_service indisponível: %s", exc)
            return None
        try:
            return upload_pdf_bytes(
                upload_id=upload_id,
                user_id=user_id,
                pdf_bytes=pdf_bytes,
                content_type=content_type,
            )
        except Exception as exc:
            logger.error("Falha ao enviar PDF ao Storage (%s): %s", upload_id, exc)
            return None

    def update_meta(self, upload_id: str, **fields: Any) -> dict[str, Any]:
        meta = self.load_meta(upload_id)
        meta.update(fields)
        self._write_meta(upload_id, meta)
        return meta

    def _write_meta(self, upload_id: str, meta: dict[str, Any]) -> None:
        self._meta_path(upload_id).write_text(
            json.dumps(meta, ensure_ascii=False),
            encoding="utf-8",
        )

    def load_meta(self, upload_id: str) -> dict[str, Any]:
        path = self._meta_path(upload_id)
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def pdf_path(self, upload_id: str) -> Path:
        return self._pdf_path(upload_id)

    def ensure_pdf(self, upload_id: str, user_id: str | None = None) -> Path:
        """Garante PDF no disco; restaura do Firebase Storage se o disco efêmero perdeu o arquivo."""
        path = self._pdf_path(upload_id)
        if path.is_file():
            return path

        meta = self.load_meta(upload_id)
        owners: list[str] = []
        for candidate in (meta.get("userId"), user_id):
            if candidate and str(candidate) not in owners:
                owners.append(str(candidate))

        if not owners:
            raise HTTPException(status_code=404, detail=f"Upload não encontrado: {upload_id}")

        try:
            from services.storage_service import download_pdf_bytes
        except Exception as exc:
            logger.warning("Storage indisponível para restaurar %s: %s", upload_id, exc)
            raise HTTPException(status_code=404, detail=f"Upload não encontrado: {upload_id}") from exc

        for owner in owners:
            cloud_bytes = download_pdf_bytes(upload_id=upload_id, user_id=owner)
            if not cloud_bytes:
                continue
            try:
                path.write_bytes(cloud_bytes)
                if not meta:
                    self._write_meta(
                        upload_id,
                        {
                            "uploadId": upload_id,
                            "userId": owner,
                            "filename": f"{upload_id}.pdf",
                            "content_type": "application/pdf",
                            "cloudUploadStatus": "completed",
                        },
                    )
                logger.info("PDF restaurado do Storage: %s", upload_id)
                return path
            except OSError as exc:
                logger.warning("Falha ao gravar PDF restaurado %s: %s", upload_id, exc)

        raise HTTPException(status_code=404, detail=f"Upload não encontrado: {upload_id}")

    def assert_access(self, upload_id: str, user_id: str) -> None:
        meta = self.load_meta(upload_id)
        owner = meta.get("userId")
        if not owner:
            return
        if str(owner) != str(user_id):
            raise HTTPException(status_code=403, detail="Acesso negado")
