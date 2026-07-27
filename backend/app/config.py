import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

IS_VERCEL = os.getenv("VERCEL", "").strip().lower() in {"1", "true", "yes", "on"}
IS_RENDER = (
    os.getenv("RENDER", "").strip().lower() in {"1", "true", "yes", "on"}
    or bool(os.getenv("RENDER_SERVICE_NAME") or os.getenv("RENDER_SERVICE_ID"))
)
# Cloud Run / Firebase (K_SERVICE é injetado automaticamente pelo Cloud Run).
IS_CLOUD_RUN = bool(os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_SERVICE")) or (
    os.getenv("IS_CLOUD_RUN", "").strip().lower() in {"1", "true", "yes", "on"}
)
IS_CLOUD = IS_VERCEL or IS_RENDER or IS_CLOUD_RUN

if not IS_CLOUD:
    load_dotenv(BASE_DIR.parent / ".env")
    load_dotenv(BASE_DIR / ".env")
    load_dotenv()

RUNTIME_BASE_DIR = Path("/tmp/thora") if IS_CLOUD else BASE_DIR / "data"
UPLOAD_DIR = RUNTIME_BASE_DIR / "uploads"
CACHE_DIR = RUNTIME_BASE_DIR / "cache"
TEMP_DIR = RUNTIME_BASE_DIR / "temp"
JOBS_DIR = RUNTIME_BASE_DIR / "jobs"

for folder in (UPLOAD_DIR, CACHE_DIR, TEMP_DIR, JOBS_DIR):
    folder.mkdir(parents=True, exist_ok=True)

ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = ENVIRONMENT == "development"

_default_max_file_size = 8 * 1024 * 1024 if IS_VERCEL else 50 * 1024 * 1024
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", _default_max_file_size))

EXTRA_FRONTEND_URLS = [
    url.strip() for url in os.getenv("FRONTEND_URLS", "").split(",") if url.strip()
]

FRONTEND_URLS = [
    url
    for url in [
        "http://localhost:5173",
        "http://localhost:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:8001",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:8001",
        "https://410-thora.netlify.app",
        "https://410-thora-construcaob.netlify.app",
        "https://410-borderles.netlify.app",
        "https://borderles-410.netlify.app",
        "https://borderless-410-thora.netlify.app",
        os.getenv("FRONTEND_URL", ""),
        *EXTRA_FRONTEND_URLS,
    ]
    if url
]

CORS_ORIGIN_REGEX = os.getenv("CORS_ORIGIN_REGEX", r"https://[\w-]+\.netlify\.app")

API_TITLE = "Thora Construção API"
API_VERSION = "2.0.0"
API_DESCRIPTION = "API para leitura de PDFs e orçamentos de obras"

# Cloud: páginas suficientes para orçamentos típicos.
_default_detect_pages = "40" if IS_CLOUD else "60"
DETECT_TABLES_MAX_PAGES = int(os.getenv("DETECT_TABLES_MAX_PAGES", _default_detect_pages))
_default_max_candidates = "30" if IS_CLOUD else "40"
DETECT_TABLES_MAX_CANDIDATES = int(
    os.getenv("DETECT_TABLES_MAX_CANDIDATES", _default_max_candidates)
)
DETECT_TABLES_THUMB_SCALE = float(os.getenv("DETECT_TABLES_THUMB_SCALE", "1.25"))
DETECT_TABLES_CACHE_VERSION = int(os.getenv("DETECT_TABLES_CACHE_VERSION", "12"))
DETECT_JOB_STALE_SECONDS = int(os.getenv("DETECT_JOB_STALE_SECONDS", "900"))

# Cloud: miniaturas estouram RAM — desligadas por padrão.
_default_skip_thumbs = "true" if IS_CLOUD else "false"
DETECT_TABLES_SKIP_THUMBNAILS = os.getenv(
    "DETECT_TABLES_SKIP_THUMBNAILS", _default_skip_thumbs
).lower() in {"1", "true", "yes", "on"}

_default_preview_width = "900" if IS_CLOUD else "3200"
TABLE_PREVIEW_TARGET_WIDTH_PX = int(
    os.getenv("TABLE_PREVIEW_TARGET_WIDTH_PX", _default_preview_width)
)
TABLE_PREVIEW_MIN_SCALE = float(
    os.getenv("TABLE_PREVIEW_MIN_SCALE", "1.0" if IS_CLOUD else "2.5")
)
TABLE_PREVIEW_MAX_SCALE = float(
    os.getenv("TABLE_PREVIEW_MAX_SCALE", "1.8" if IS_CLOUD else "6.0")
)
TABLE_PREVIEW_PAGE_SCALE = float(
    os.getenv("TABLE_PREVIEW_PAGE_SCALE", "1.0" if IS_CLOUD else "2.5")
)

_default_disable_camelot = "true" if IS_CLOUD else "false"
DISABLE_CAMELOT = os.getenv("DISABLE_CAMELOT", _default_disable_camelot).lower() in {
    "1",
    "true",
    "yes",
    "on",
}

FIREBASE_DISABLED = os.getenv("FIREBASE_DISABLED", "").lower() in {"1", "true", "yes", "on"}
FIREBASE_CREDENTIALS = os.getenv("FIREBASE_CREDENTIALS", "")
FIREBASE_STORAGE_BUCKET = os.getenv(
    "FIREBASE_STORAGE_BUCKET",
    "borderless-5a4c8.firebasestorage.app",
).strip()
