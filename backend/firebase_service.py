"""
Firebase Firestore integration for persisting extracted data
"""

import firebase_admin
from firebase_admin import credentials
from firebase_admin import firestore
from datetime import datetime
from typing import List, Dict, Any, Optional
import logging
import json
import copy
import uuid
from pathlib import Path
import os

from config import FIREBASE_STORAGE_BUCKET

logger = logging.getLogger(__name__)

_SERVICE_ACCOUNT_REQUIRED_FIELDS = (
    "type",
    "project_id",
    "private_key",
    "client_email",
    "token_uri",
)


def _parse_firebase_credentials_env(raw: str) -> dict[str, Any]:
    """Interpreta FIREBASE_CREDENTIALS (JSON inline ou base64)."""
    import base64

    text = raw.strip()
    if not text:
        raise ValueError("FIREBASE_CREDENTIALS vazio")

    if not text.startswith("{"):
        try:
            text = base64.b64decode(text).decode("utf-8").strip()
        except Exception as exc:
            raise ValueError(
                "FIREBASE_CREDENTIALS deve ser o JSON completo da service account "
                "(Firebase Console → Project Settings → Service accounts → Generate new private key) "
                "ou o mesmo JSON codificado em base64."
            ) from exc

    creds_dict = json.loads(text)
    missing = [field for field in _SERVICE_ACCOUNT_REQUIRED_FIELDS if not creds_dict.get(field)]
    if missing:
        raise ValueError(
            "JSON incompleto — campos ausentes: "
            + ", ".join(missing)
            + ". Use o arquivo .json da service account (não a config do app web)."
        )
    return creds_dict


# Initialize Firebase
db = None
_firebase_disabled = os.getenv("FIREBASE_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}

if _firebase_disabled:
    logger.warning("⚠️  Firebase desativado por FIREBASE_DISABLED. Rodando em modo offline.")
    db = None
else:
    try:
        # Check if Firebase is already initialized
        firebase_admin.get_app()
        db = firestore.client()
        logger.info("✅ Firebase já inicializado")
    except ValueError:
        # Firebase not initialized, try to initialize
        try:
            # Try to load from environment variable first (Render)
            firebase_creds_env = os.getenv("FIREBASE_CREDENTIALS")
            storage_options = (
                {"storageBucket": FIREBASE_STORAGE_BUCKET}
                if FIREBASE_STORAGE_BUCKET
                else None
            )
            if firebase_creds_env:
                creds_dict = _parse_firebase_credentials_env(firebase_creds_env)
                creds = credentials.Certificate(creds_dict)
                firebase_admin.initialize_app(creds, storage_options)
                db = firestore.client()
                logger.info("✅ Firebase initialized com environment variable")
            else:
                # Fallback to local file
                creds_path = Path(__file__).parent / "firebase_credentials.json"
                if creds_path.exists():
                    creds = credentials.Certificate(str(creds_path))
                    firebase_admin.initialize_app(creds, storage_options)
                    db = firestore.client()
                    logger.info("✅ Firebase initialized com credentials file")
                else:
                    # Cloud Run / GCP Application Default Credentials
                    try:
                        creds = credentials.ApplicationDefault()
                        firebase_admin.initialize_app(creds, storage_options)
                        db = firestore.client()
                        logger.info("✅ Firebase initialized com Application Default Credentials")
                    except Exception as adc_exc:
                        logger.warning(
                            "⚠️  Sem credentials file/env/ADC (%s). Rodando em modo offline.",
                            adc_exc,
                        )
                        db = None
        except Exception as e:
            logger.error(f"❌ Firebase initialization failed: {e}")
            db = None
    except Exception as e:
        logger.error(f"❌ Error initializing Firebase: {e}")
        db = None


class OrcamentoFirestore:
    """Manage Orçamento data in Firestore"""
    
    COLLECTION = "orcamentos"
    
    @staticmethod
    def save_orcamento(
        user_id: str,
        upload_id: str,
        filename: str,
        tables: List[Dict[str, Any]],
        items_data: Dict[str, Any] = None,
        ia_metadata: Dict[str, Any] = None,
        storage_url: str | None = None,
    ) -> str:
        """
        Save extracted PDF data to Firestore
        
        Args:
            upload_id: Unique upload identifier
            filename: Original PDF filename
            tables: Extracted tables from PDF
            items_data: Parsed items data (optional)
            
        Returns:
            Document ID in Firestore
        """
        if not db:
            logger.warning("⚠️  Firestore not initialized - running in offline mode")
            return upload_id
        
        try:
            doc_data = {
                "userId": user_id,
                "uploadId": upload_id,
                "filename": filename,
                "uploadedAt": datetime.now(),
                "extractedAt": datetime.now(),
                "tables": tables,
                "itemsData": items_data or {},
                "tablesFound": len(tables),
                "status": "completed",
            }
            if ia_metadata:
                doc_data["ia_metadata"] = ia_metadata
            if storage_url:
                doc_data["storageUrl"] = storage_url

            # Add document to collection
            db.collection("orcamentos").add(doc_data)
            logger.info(f"✅ Orçamento salvo no Firebase: {upload_id}")
            return upload_id
            
        except Exception as e:
            logger.error(f"❌ Erro ao salvar no Firestore: {str(e)}")
            raise
    
    @staticmethod
    def get_orcamento_by_upload_id(upload_id: str, user_id: str = None) -> Dict[str, Any]:
        """
        Get orçamento by upload ID
        
        Args:
            upload_id: Upload identifier
            
        Returns:
            Orçamento document or None
        """
        if not db:
            return None
        
        try:
            query_ref = db.collection("orcamentos").where("uploadId", "==", upload_id)
            if user_id:
                query_ref = query_ref.where("userId", "==", user_id)
            docs = query_ref.stream()
            
            for doc in docs:
                return {
                    "id": doc.id,
                    **doc.to_dict()
                }
            
            return None
            
        except Exception as e:
            logger.error(f"❌ Erro ao buscar orçamento: {str(e)}")
            return None
    
    @staticmethod
    def list_all_orcamentos(user_id: str = None) -> List[Dict[str, Any]]:
        """
        List all orçamentos
        
        Returns:
            List of orçamento documents
        """
        if not db:
            return []
        
        try:
            query_ref = db.collection("orcamentos")
            if user_id:
                query_ref = query_ref.where("userId", "==", user_id)
            docs = query_ref.stream()
            return [
                {
                    "id": doc.id,
                    **doc.to_dict()
                }
                for doc in docs
            ]
            
        except Exception as e:
            logger.error(f"❌ Erro ao listar orçamentos: {str(e)}")
            return []
    
    @staticmethod
    def update_orcamento(doc_id: str, data: Dict[str, Any]) -> bool:
        """
        Update orçamento document
        
        Args:
            doc_id: Document ID
            data: Data to update
            
        Returns:
            Success status
        """
        if not db:
            return False
        
        try:
            data["updatedAt"] = datetime.now()
            db.collection("orcamentos").document(doc_id).update(data)
            logger.info(f"✅ Orçamento atualizado: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erro ao atualizar orçamento: {str(e)}")
            return False
    
    @staticmethod
    def save_upload_record(
        user_id: str,
        upload_id: str,
        filename: str,
        storage_url: str | None = None,
        size_bytes: int | None = None,
    ) -> str:
        """Registra metadados do upload (PDF original) no Firestore."""
        if not db:
            return upload_id

        try:
            doc_data = {
                "userId": user_id,
                "uploadId": upload_id,
                "filename": filename,
                "uploadedAt": datetime.now(),
                "status": "uploaded",
            }
            if storage_url:
                doc_data["storageUrl"] = storage_url
            if size_bytes is not None:
                doc_data["sizeBytes"] = size_bytes
            db.collection("uploads").add(doc_data)
            logger.info("Upload registrado no Firestore: %s", upload_id)
            return upload_id
        except Exception as e:
            logger.error("Erro ao registrar upload no Firestore: %s", e)
            return upload_id

    @staticmethod
    def delete_orcamento(doc_id: str) -> bool:
        """
        Delete orçamento document
        
        Args:
            doc_id: Document ID
            
        Returns:
            Success status
        """
        if not db:
            return False
        
        try:
            db.collection("orcamentos").document(doc_id).delete()
            logger.info(f"✅ Orçamento deletado: {doc_id}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Erro ao deletar orçamento: {str(e)}")
            return False


class OrcamentoEnterpriseFirestore:
    """Auditoria, versionamento e locks de concorrência por projeto (upload_id)."""

    AUDIT_COLLECTION = "audit_logs"
    VERSIONS_SUBCOLLECTION = "budget_versions"
    LOCKS_SUBCOLLECTION = "active_locks"

    @staticmethod
    def _project_ref(project_id: str):
        if not db:
            return None
        return db.collection("projects").document(project_id)

    @staticmethod
    def save_audit_log(
        project_id: str,
        user_id: str,
        user_name: str,
        item_codigo: str,
        campo_alterado: str,
        valor_antigo: str | float | int | None,
        valor_novo: str | float | int | None,
    ) -> Optional[str]:
        if not db:
            logger.warning("Firestore offline — audit log não persistido")
            return None

        try:
            doc_data = {
                "project_id": project_id,
                "user_id": user_id,
                "user_name": user_name,
                "timestamp": datetime.now(),
                "item_codigo": item_codigo,
                "campo_alterado": campo_alterado,
                "valor_antigo": valor_antigo,
                "valor_novo": valor_novo,
            }
            _, doc_ref = db.collection(OrcamentoEnterpriseFirestore.AUDIT_COLLECTION).add(doc_data)
            return doc_ref.id
        except Exception as e:
            logger.error("Erro ao salvar audit log: %s", e)
            raise

    @staticmethod
    def save_budget_version(
        project_id: str,
        version_name: str,
        items_snapshot: List[Dict[str, Any]],
        created_by: str,
        created_by_name: str,
    ) -> Optional[Dict[str, Any]]:
        if not db:
            return None

        try:
            version_id = str(uuid.uuid4())
            doc_data = {
                "id": version_id,
                "project_id": project_id,
                "version_name": version_name,
                "items_snapshot": copy.deepcopy(items_snapshot),
                "created_at": datetime.now(),
                "created_by": created_by,
                "created_by_name": created_by_name,
            }
            ref = (
                OrcamentoEnterpriseFirestore._project_ref(project_id)
                .collection(OrcamentoEnterpriseFirestore.VERSIONS_SUBCOLLECTION)
                .document(version_id)
            )
            ref.set(doc_data)
            return doc_data
        except Exception as e:
            logger.error("Erro ao salvar versão do orçamento: %s", e)
            raise

    @staticmethod
    def list_budget_versions(project_id: str) -> List[Dict[str, Any]]:
        if not db:
            return []

        try:
            docs = (
                OrcamentoEnterpriseFirestore._project_ref(project_id)
                .collection(OrcamentoEnterpriseFirestore.VERSIONS_SUBCOLLECTION)
                .order_by("created_at", direction=firestore.Query.DESCENDING)
                .stream()
            )
            versions = []
            for doc in docs:
                data = doc.to_dict() or {}
                versions.append({"id": doc.id, **data})
            return versions
        except Exception as e:
            logger.error("Erro ao listar versões: %s", e)
            return []

    @staticmethod
    def acquire_lock(
        project_id: str,
        item_id: str,
        user_id: str,
        user_name: str,
    ) -> Dict[str, Any]:
        if not db:
            return {"status": "ok", "offline": True}

        try:
            ref = (
                OrcamentoEnterpriseFirestore._project_ref(project_id)
                .collection(OrcamentoEnterpriseFirestore.LOCKS_SUBCOLLECTION)
                .document(item_id)
            )
            doc = ref.get()
            now = datetime.now()

            if doc.exists:
                existing = doc.to_dict() or {}
                owner = str(existing.get("user_id", ""))
                if owner and owner != str(user_id):
                    return {
                        "status": "locked",
                        "locked_by": owner,
                        "locked_by_name": existing.get("user_name", "Outro usuário"),
                    }

            ref.set(
                {
                    "item_id": item_id,
                    "user_id": user_id,
                    "user_name": user_name,
                    "locked_at": now,
                }
            )
            return {"status": "ok"}
        except Exception as e:
            logger.error("Erro ao adquirir lock: %s", e)
            raise

    @staticmethod
    def release_lock(project_id: str, item_id: str, user_id: str) -> bool:
        if not db:
            return True

        try:
            ref = (
                OrcamentoEnterpriseFirestore._project_ref(project_id)
                .collection(OrcamentoEnterpriseFirestore.LOCKS_SUBCOLLECTION)
                .document(item_id)
            )
            doc = ref.get()
            if not doc.exists:
                return True

            existing = doc.to_dict() or {}
            owner = str(existing.get("user_id", ""))
            if owner and owner != str(user_id):
                return False

            ref.delete()
            return True
        except Exception as e:
            logger.error("Erro ao liberar lock: %s", e)
            raise
