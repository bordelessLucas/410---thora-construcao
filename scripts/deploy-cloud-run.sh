#!/usr/bin/env bash
# Deploy da API Thora para Cloud Run (projeto Firebase borderless-5a4c8).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ID="${GCP_PROJECT_ID:-borderless-5a4c8}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
SERVICE="${CLOUD_RUN_SERVICE:-thora-api}"
MEMORY="${CLOUD_RUN_MEMORY:-2Gi}"
CPU="${CLOUD_RUN_CPU:-1}"
TIMEOUT="${CLOUD_RUN_TIMEOUT:-900}"
MIN_INSTANCES="${CLOUD_RUN_MIN_INSTANCES:-1}"
MAX_INSTANCES="${CLOUD_RUN_MAX_INSTANCES:-1}"

export PATH="${ROOT}/.tools/google-cloud-sdk/bin:${HOME}/google-cloud-sdk/bin:${PATH}"

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud não encontrado. Esperado em ${ROOT}/.tools/google-cloud-sdk/bin"
  echo "Ou: brew install --cask google-cloud-sdk"
  exit 1
fi

echo "==> Projeto: ${PROJECT_ID} | Região: ${REGION} | Serviço: ${SERVICE}"
gcloud config set project "${PROJECT_ID}"

# APIs necessárias
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  --project="${PROJECT_ID}"

FRONTEND_URLS_DEFAULT="https://borderless-5a4c8.web.app,https://borderless-5a4c8.firebaseapp.com,https://410-thora-construcaob.netlify.app,https://410-thora.netlify.app,https://borderless-410-thora.netlify.app"

ENV_FILE="${ROOT}/backend/cloudrun.env.yaml"

SECRETS_ARGS=()
if gcloud secrets describe OPENAI_API_KEY --project="${PROJECT_ID}" >/dev/null 2>&1; then
  SECRETS_ARGS+=(--set-secrets="OPENAI_API_KEY=OPENAI_API_KEY:latest")
fi
if gcloud secrets describe FIREBASE_CREDENTIALS --project="${PROJECT_ID}" >/dev/null 2>&1; then
  SECRETS_ARGS+=(--set-secrets="FIREBASE_CREDENTIALS=FIREBASE_CREDENTIALS:latest")
fi

echo "==> Build & deploy Cloud Run…"
gcloud run deploy "${SERVICE}" \
  --source="${ROOT}/backend" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --platform=managed \
  --allow-unauthenticated \
  --memory="${MEMORY}" \
  --cpu="${CPU}" \
  --timeout="${TIMEOUT}" \
  --concurrency=40 \
  --min-instances="${MIN_INSTANCES}" \
  --max-instances="${MAX_INSTANCES}" \
  --no-cpu-throttling \
  --cpu-boost \
  --env-vars-file="${ENV_FILE}" \
  "${SECRETS_ARGS[@]}"

URL=$(gcloud run services describe "${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(status.url)')

echo ""
echo "==> Cloud Run URL: ${URL}"
echo "==> Frontend: Firebase Hosting (https://${PROJECT_ID}.web.app) com VITE_API_URL=${URL}"
echo "==> Teste: curl -s ${URL}/health"
