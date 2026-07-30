"""
Upload e download de PDFs no Firebase Storage (assíncrono em background).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from config import FIREBASE_STORAGE_BUCKET

logger = logging.getLogger(__name__)

_bucket = None
_storage_disabled = False


def is_storage_available() -> bool:
    """Indica se o Firebase Storage está configurado e acessível."""
    return _get_bucket() is not None


def _get_bucket():
    global _bucket, _storage_disabled
    if _storage_disabled:
        return None
    if _bucket is not None:
        return _bucket

    try:
        import firebase_admin
        from firebase_admin import storage

        try:
            firebase_admin.get_app()
        except ValueError:
            logger.warning("Firebase Storage: app não inicializado — upload em nuvem desativado")
            _storage_disabled = True
            return None

        bucket_name = FIREBASE_STORAGE_BUCKET
        if bucket_name:
            _bucket = storage.bucket(bucket_name)
        else:
            _bucket = storage.bucket()
        return _bucket
    except Exception as exc:
        logger.warning("Firebase Storage indisponível: %s", exc)
        _storage_disabled = True
        return None


def _storage_path(user_id: str, upload_id: str) -> str:
    safe_user = (user_id or "anonymous").replace("/", "_")
    return f"uploads/{safe_user}/{upload_id}.pdf"


def upload_pdf_bytes(
    *,
    upload_id: str,
    user_id: str,
    pdf_bytes: bytes,
    content_type: str = "application/pdf",
) -> Optional[str]:
    """
    Envia PDF para o bucket. Retorna URL pública/signed ou None se indisponível.
    """
    bucket = _get_bucket()
    if not bucket:
        return None

    path = _storage_path(user_id, upload_id)
    try:
        blob = bucket.blob(path)
        blob.upload_from_string(pdf_bytes, content_type=content_type)
        try:
            blob.make_public()
            url = blob.public_url
        except Exception:
            url = blob.generate_signed_url(expiration=60 * 60 * 24 * 365 * 10)
        logger.info("PDF salvo no Storage: %s", path)
        return url
    except Exception as exc:
        logger.error("Erro ao enviar PDF ao Storage (%s): %s", upload_id, exc)
        return None


def download_pdf_bytes(*, upload_id: str, user_id: str) -> Optional[bytes]:
    """Baixa PDF do Storage quando o arquivo local não existe."""
    bucket = _get_bucket()
    if not bucket:
        return None

    path = _storage_path(user_id, upload_id)
    try:
        blob = bucket.blob(path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as exc:
        logger.warning("Erro ao baixar PDF do Storage (%s): %s", upload_id, exc)
        return None


async def upload_pdf_bytes_async(
    *,
    upload_id: str,
    user_id: str,
    pdf_bytes: bytes,
    content_type: str = "application/pdf",
) -> Optional[str]:
    """Wrapper assíncrono para não bloquear o event loop."""
    return await asyncio.to_thread(
        upload_pdf_bytes,
        upload_id=upload_id,
        user_id=user_id,
        pdf_bytes=pdf_bytes,
        content_type=content_type,
    )


async def download_pdf_bytes_async(*, upload_id: str, user_id: str) -> Optional[bytes]:
    return await asyncio.to_thread(
        download_pdf_bytes,
        upload_id=upload_id,
        user_id=user_id,
    )


def upload_bytes(
    storage_path: str,
    data: bytes,
    *,
    content_type: str = "application/octet-stream",
) -> bool:
    """Upload genérico (ex.: cache de tabelas JSON) para o bucket."""
    bucket = _get_bucket()
    if not bucket:
        return False
    try:
        blob = bucket.blob(storage_path)
        blob.upload_from_string(data, content_type=content_type)
        logger.info("Blob salvo no Storage: %s (%s bytes)", storage_path, len(data))
        return True
    except Exception as exc:
        logger.error("Erro ao enviar blob %s: %s", storage_path, exc)
        return False


def download_bytes(storage_path: str) -> Optional[bytes]:
    bucket = _get_bucket()
    if not bucket:
        return None
    try:
        blob = bucket.blob(storage_path)
        if not blob.exists():
            return None
        return blob.download_as_bytes()
    except Exception as exc:
        logger.warning("Erro ao baixar blob %s: %s", storage_path, exc)
        return None


def delete_blob(storage_path: str) -> bool:
    bucket = _get_bucket()
    if not bucket:
        return False
    try:
        blob = bucket.blob(storage_path)
        if blob.exists():
            blob.delete()
        return True
    except Exception as exc:
        logger.warning("Erro ao apagar blob %s: %s", storage_path, exc)
        return False
