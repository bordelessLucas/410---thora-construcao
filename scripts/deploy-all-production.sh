#!/usr/bin/env bash
# Deploy completo Thora → Cloud Run + Firebase (rules + Hosting)
# Rode no Terminal do Mac (fora do sandbox), com login ativo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PROJECT_ID="${GCP_PROJECT_ID:-borderless-5a4c8}"
REGION="${CLOUD_RUN_REGION:-us-central1}"
SERVICE="${CLOUD_RUN_SERVICE:-thora-api}"
HOSTING_URL_PRIMARY="https://${PROJECT_ID}.web.app"
HOSTING_URL_ALT="https://${PROJECT_ID}.firebaseapp.com"

# SDK local do repo, depois install padrão do usuário
export PATH="${ROOT}/.tools/google-cloud-sdk/bin:${HOME}/google-cloud-sdk/bin:${PATH}"

echo "==> 1/4 Git: garantir main atualizado"
git checkout main
git pull --ff-only origin main || true
git status -sb

echo ""
echo "==> 2/4 Cloud Run (API)"
if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud não encontrado."
  echo "Opção A: use o SDK do repo (já em .tools/) — reinicie o script após este fix."
  echo "Opção B: brew install --cask google-cloud-sdk"
  echo "Opção C: https://cloud.google.com/sdk/docs/install"
  exit 1
fi
echo "gcloud: $(command -v gcloud)"
gcloud auth list
gcloud config set project "${PROJECT_ID}"
chmod +x scripts/deploy-cloud-run.sh
./scripts/deploy-cloud-run.sh
API_URL=$(gcloud run services describe "${SERVICE}" \
  --region="${REGION}" \
  --project="${PROJECT_ID}" \
  --format='value(status.url)')
API_URL="${API_URL%/}"
echo "API: ${API_URL}"
curl -fsS "${API_URL}/health" || true
echo ""

echo ""
echo "==> 3/4 Firebase (rules + Storage)"
if ! command -v firebase >/dev/null 2>&1; then
  echo "Instale o Firebase CLI: npm install -g firebase-tools"
  exit 1
fi
firebase login --reauth || true
firebase use "${PROJECT_ID}"
firebase deploy --only firestore:rules,storage --project "${PROJECT_ID}"

echo ""
echo "==> 4/4 Firebase Hosting (frontend)"
# NÃO use rewrite Hosting→Cloud Run para /api: timeout 60s quebra PDF/IA.
# O bundle precisa da URL do Cloud Run em build-time.
echo "VITE_API_URL=${API_URL}" > frontend/.env.production
(cd frontend && npm ci && VITE_API_URL="${API_URL}" npm run build)
firebase deploy --only hosting --project "${PROJECT_ID}"

echo ""
echo "==== DEPLOY OK ===="
echo "API:       ${API_URL}"
echo "Health:    curl -s ${API_URL}/health"
echo "Frontend:  ${HOSTING_URL_PRIMARY}"
echo "           ${HOSTING_URL_ALT}"
echo "Firebase:  projeto ${PROJECT_ID} (Auth / Firestore / Storage / Hosting)"
echo ""
echo "Auth: confirme Authorized domains com ${PROJECT_ID}.web.app e ${PROJECT_ID}.firebaseapp.com"
echo "      (Console → Authentication → Settings → Authorized domains)"
echo "Smoke: abra ${HOSTING_URL_PRIMARY} → login → upload PDF → processar"
