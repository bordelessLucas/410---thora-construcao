#!/usr/bin/env bash
# Deploy completo Thora → Cloud Run + Firebase Rules + Netlify
# Rode no Terminal do Mac (fora do sandbox), com login ativo.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export PATH="${HOME}/google-cloud-sdk/bin:${PATH}"

echo "==> 1/4 Git: garantir main atualizado"
git checkout main
git pull --ff-only origin main || true
git status -sb

echo ""
echo "==> 2/4 Cloud Run (API)"
if ! command -v gcloud >/dev/null 2>&1; then
  echo "Instale o gcloud: https://cloud.google.com/sdk/docs/install"
  exit 1
fi
gcloud auth list
gcloud config set project borderless-5a4c8
chmod +x scripts/deploy-cloud-run.sh
./scripts/deploy-cloud-run.sh
API_URL=$(gcloud run services describe thora-api \
  --region=us-central1 \
  --project=borderless-5a4c8 \
  --format='value(status.url)')
echo "API: ${API_URL}"
curl -fsS "${API_URL}/health" || true
echo ""

echo ""
echo "==> 3/4 Firebase (rules Firestore + Storage)"
npx -y firebase-tools@latest login --reauth || true
npx -y firebase-tools@latest use borderless-5a4c8
npx -y firebase-tools@latest deploy --only firestore:rules,storage --project borderless-5a4c8

echo ""
echo "==> 4/4 Netlify (frontend produção)"
if command -v netlify >/dev/null 2>&1; then
  netlify login || true
  netlify link || true
  netlify env:set VITE_API_URL "${API_URL}" --context production || true
  (cd frontend && npm ci && npm run build)
  netlify deploy --prod --dir=frontend/dist
else
  echo "Netlify CLI ausente. Se o site está ligado ao GitHub, o push em main já dispara o build."
  echo "Confirme no painel: https://app.netlify.com — VITE_API_URL=${API_URL}"
fi

echo ""
echo "==== DEPLOY OK ===="
echo "API:      ${API_URL}"
echo "Health:   curl -s ${API_URL}/health"
echo "Frontend: https://410-borderles.netlify.app (ou o domínio do site Netlify)"
echo "Firebase: projeto borderless-5a4c8 (Auth/Firestore/Storage)"
