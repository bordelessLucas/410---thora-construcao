import os
from pathlib import Path

from dotenv import load_dotenv

# Paths
BASE_DIR = Path(__file__).resolve().parent

# Detectar plataforma ANTES do dotenv (Render injeta RENDER / RENDER_SERVICE_NAME no processo)
IS_VERCEL = os.getenv("VERCEL", "").strip().lower() in {"1", "true", "yes", "on"}
IS_RENDER = (
    os.getenv("RENDER", "").strip().lower() in {"1", "true", "yes", "on"}
    or bool(os.getenv("RENDER_SERVICE_NAME") or os.getenv("RENDER_SERVICE_ID"))
)

# Em Render/Vercel usar só variáveis da plataforma — evita .env local vazio sobrescrever nada
if not IS_VERCEL and not IS_RENDER:
    load_dotenv(BASE_DIR.parent / ".env")
    load_dotenv(BASE_DIR / ".env")
    load_dotenv()
RUNTIME_BASE_DIR = Path("/tmp") if (IS_VERCEL or IS_RENDER) else BASE_DIR
UPLOAD_FOLDER = RUNTIME_BASE_DIR / "uploads"
TEMP_FOLDER = RUNTIME_BASE_DIR / "temp"
CACHE_FOLDER = RUNTIME_BASE_DIR / "cache"

# Criar pastas se não existirem
UPLOAD_FOLDER.mkdir(exist_ok=True)
TEMP_FOLDER.mkdir(exist_ok=True)
CACHE_FOLDER.mkdir(exist_ok=True)

# Ambiente
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
DEBUG = ENVIRONMENT == "development"

# Upload
_default_max_file_size = 8 * 1024 * 1024 if IS_VERCEL else 50 * 1024 * 1024
MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", _default_max_file_size))

# CORS
EXTRA_FRONTEND_URLS = [
    url.strip()
    for url in os.getenv("FRONTEND_URLS", "").split(",")
    if url.strip()
]

FRONTEND_URLS = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8001",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:8001",
    "https://borderless-5a4c8.web.app",
    "https://borderless-5a4c8.firebaseapp.com",
    "https://410-thora.netlify.app",
    "https://borderles-410.netlify.app",
    "https://borderless-410-thora.netlify.app",
    os.getenv("FRONTEND_URL", ""),
    *EXTRA_FRONTEND_URLS,
]
FRONTEND_URLS = [url for url in FRONTEND_URLS if url]

# Netlify + Firebase Hosting (web.app / firebaseapp.com)
CORS_ORIGIN_REGEX = os.getenv(
    "CORS_ORIGIN_REGEX",
    r"https://[\w-]+\.(netlify\.app|web\.app|firebaseapp\.com)",
)

# Server
API_TITLE = "Automação de Orçamentos"
API_VERSION = "1.0.0"
API_DESCRIPTION = "API para processar e gerar orçamentos de obras"

_PLACEHOLDER_API_KEYS = frozenset(
    {
        "",
        "sua-chave-aqui",
        "sua-chave",
        "your-key-here",
        "changeme",
        "...",
    }
)


def _normalize_api_key(key: str) -> str:
    """Remove quebras de linha e espaços acidentais ao colar chaves no painel Render."""
    return "".join((key or "").split())


def _read_env_secret_key(name: str, *, aliases: tuple[str, ...] = ()) -> str:
    """Lê variável de ambiente, aliases ou Secret File do Render (/etc/secrets/<name>)."""
    for key in (name, *aliases):
        raw = os.getenv(key)
        if raw:
            return _normalize_api_key(raw)
        file_env = os.getenv(f"{key}_FILE")
        if file_env:
            try:
                return _normalize_api_key(Path(file_env).read_text(encoding="utf-8"))
            except OSError:
                pass
        for secret_path in (
            Path("/etc/secrets") / key,
            Path("/var/secrets") / key,
        ):
            if secret_path.is_file():
                try:
                    return _normalize_api_key(secret_path.read_text(encoding="utf-8"))
                except OSError:
                    pass
    return ""


def _scan_env_key_names() -> list[str]:
    """Lista nomes de env vars relevantes (sem expor valores)."""
    needles = ("OPENAI", "GEMINI", "FIREBASE", "RENDER", "ENVIRONMENT", "FRONTEND")
    return sorted(k for k in os.environ if any(n in k.upper() for n in needles))


# AI (Google Gemini)
GEMINI_API_KEY = _read_env_secret_key("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")

# AI fallback providers (OpenAI-compatible APIs)
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen3-14b:free")

GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

OPENAI_API_KEY = _read_env_secret_key("OPENAI_API_KEY", aliases=("OPENAI_KEY",))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
# Fluxo Orçamento Analítico (GPT-4o) — chave via variável de ambiente (Render / .env local)
OPENAI_ORCAMENTO_MODEL = os.getenv("OPENAI_ORCAMENTO_MODEL", "gpt-4o")
_default_orcamento_timeout = "55" if IS_VERCEL else "120"
OPENAI_ORCAMENTO_TIMEOUT_SECONDS = float(
    os.getenv("OPENAI_ORCAMENTO_TIMEOUT", _default_orcamento_timeout)
)

def _is_valid_env_key(key: str | None) -> bool:
    normalized = _normalize_api_key(key or "").lower()
    return bool(normalized) and normalized not in _PLACEHOLDER_API_KEYS


def get_openai_api_key() -> str:
    """Lê OPENAI_API_KEY em runtime (Render injeta via Environment, não usa .env)."""
    return _read_env_secret_key("OPENAI_API_KEY", aliases=("OPENAI_KEY",)) or _normalize_api_key(
        OPENAI_API_KEY
    )


def get_gemini_api_key() -> str:
    """Lê GEMINI_API_KEY em runtime."""
    return _read_env_secret_key("GEMINI_API_KEY") or _normalize_api_key(GEMINI_API_KEY)


def is_openai_configured() -> bool:
    return _is_valid_env_key(get_openai_api_key())


def is_gemini_configured() -> bool:
    return _is_valid_env_key(get_gemini_api_key())


def resolve_ai_extraction_provider() -> str | None:
    if is_openai_configured():
        return "openai"
    if is_gemini_configured():
        return "gemini"
    return None


def ai_keys_status() -> dict[str, object]:
    provider = resolve_ai_extraction_provider()
    configured = provider is not None
    hint = None
    if not configured:
        if IS_RENDER:
            hint = (
                "Configure OPENAI_API_KEY ou GEMINI_API_KEY em "
                "Render → four10-thora-construcao-262l → Environment → Save → Manual Deploy."
            )
        else:
            hint = "Defina OPENAI_API_KEY ou GEMINI_API_KEY no .env (raiz ou backend/)."
    status: dict[str, object] = {
        "openai_configured": is_openai_configured(),
        "gemini_configured": is_gemini_configured(),
        "extraction_provider": provider,
        "configured": configured,
        "hint": hint,
    }
    if IS_RENDER or ENVIRONMENT == "production":
        openai_key = get_openai_api_key()
        firebase_raw = os.getenv("FIREBASE_CREDENTIALS") or ""
        status["diagnostics"] = {
            "is_render": IS_RENDER,
            "render_service": os.getenv("RENDER_SERVICE_NAME", ""),
            "render_external_url": os.getenv("RENDER_EXTERNAL_URL", ""),
            "environment_var": os.getenv("ENVIRONMENT", ""),
            "frontend_url_set": bool(os.getenv("FRONTEND_URL")),
            "openai_env_var_length": len(os.getenv("OPENAI_API_KEY") or ""),
            "openai_key_alias_length": len(os.getenv("OPENAI_KEY") or ""),
            "openai_normalized_length": len(openai_key),
            "openai_starts_with_sk": openai_key.startswith("sk-"),
            "openai_secret_file": Path("/etc/secrets/OPENAI_API_KEY").is_file(),
            "openai_orcamento_model": os.getenv("OPENAI_ORCAMENTO_MODEL", ""),
            "orcamento_timeout_set": bool(os.getenv("OPENAI_ORCAMENTO_TIMEOUT")),
            "firebase_credentials_length": len(firebase_raw),
            "matching_env_keys": _scan_env_key_names(),
        }
    return status

# AI local provider (Ollama)
_default_ollama_enabled = "false" if IS_VERCEL else "true"
OLLAMA_ENABLED = os.getenv("OLLAMA_ENABLED", _default_ollama_enabled).lower() in (
    "1",
    "true",
    "yes",
    "on",
)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
_default_ollama_timeout = "55" if IS_VERCEL else "45"
OLLAMA_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_TIMEOUT_SECONDS", _default_ollama_timeout))

_default_ai_provider_timeout = "55" if IS_VERCEL else "45"
AI_PROVIDER_TIMEOUT_SECONDS = float(
    os.getenv("AI_PROVIDER_TIMEOUT_SECONDS", _default_ai_provider_timeout)
)

_default_multi_provider_chain = "false" if IS_VERCEL else "true"
ENABLE_MULTI_PROVIDER_CHAIN = os.getenv(
    "ENABLE_MULTI_PROVIDER_CHAIN", _default_multi_provider_chain
).lower() in ("1", "true", "yes", "on")

# Firebase Storage (PDFs originais)
FIREBASE_STORAGE_BUCKET = os.getenv(
    "FIREBASE_STORAGE_BUCKET",
    "borderless-5a4c8.firebasestorage.app",
)

# Detecção de tabelas em PDF (limite de páginas para pdfplumber/Camelot)
_default_detect_pages = "10" if IS_RENDER else ("20" if ENVIRONMENT == "production" else "60")
DETECT_TABLES_MAX_PAGES = int(os.getenv("DETECT_TABLES_MAX_PAGES", _default_detect_pages))
DETECT_TABLES_MAX_CANDIDATES = int(os.getenv("DETECT_TABLES_MAX_CANDIDATES", "20"))
DETECT_TABLES_THUMB_SCALE = float(os.getenv("DETECT_TABLES_THUMB_SCALE", "1.0"))
DETECT_TABLES_CACHE_VERSION = int(os.getenv("DETECT_TABLES_CACHE_VERSION", "2"))
# Camelot + OpenCV consomem muita RAM — no Render free tier costuma derrubar o worker (502).
_default_disable_camelot = "true" if IS_RENDER else "false"
DISABLE_CAMELOT = os.getenv("DISABLE_CAMELOT", _default_disable_camelot).lower() in (
    "1",
    "true",
    "yes",
    "on",
)

# Redis / Celery (fila persistente de Orçamento Analítico)
REDIS_URL = os.getenv("REDIS_URL", "").strip()
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", REDIS_URL).strip()
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", REDIS_URL).strip()
_default_use_celery = "false" if (IS_VERCEL or IS_RENDER) else "true"
USE_CELERY_QUEUE = os.getenv("USE_CELERY_QUEUE", _default_use_celery).lower() in (
    "1",
    "true",
    "yes",
    "on",
) and bool(CELERY_BROKER_URL) and not IS_VERCEL

if is_gemini_configured():
    print("GEMINI_API_KEY carregada")
else:
    print("AVISO: GEMINI_API_KEY não encontrada ou inválida")

if is_openai_configured():
    print(f"OPENAI_API_KEY carregada (len={len(get_openai_api_key())})")
elif not is_gemini_configured():
    raw_len = len(os.getenv("OPENAI_API_KEY") or "")
    print(
        "AVISO: configure OPENAI_API_KEY ou GEMINI_API_KEY no ambiente "
        f"(Render ou .env local; OPENAI_API_KEY raw len={raw_len})"
    )
