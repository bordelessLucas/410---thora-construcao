# Cloud Run — API Thora (FastAPI)
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080 \
    ENVIRONMENT=production \
    K_SERVICE=thora-api \
    DETECT_TABLES_SKIP_THUMBNAILS=true \
    DISABLE_CAMELOT=true \
    DETECT_TABLES_MAX_PAGES=40 \
    USE_CELERY_QUEUE=false

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    ghostscript \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
# Evita instalar stack pesada de Camelot/CV no container (desligado via env).
RUN grep -vE '^(camelot-py|opencv)' requirements.txt > requirements.cloud.txt \
    && pip install --upgrade pip \
    && pip install -r requirements.cloud.txt

COPY . .

# Cloud Run injeta PORT; um worker evita OOM em 1–2 GiB.
CMD exec gunicorn -w 1 --timeout 600 --graceful-timeout 30 \
    -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:${PORT} \
    main:app
