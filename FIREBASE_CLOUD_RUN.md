# Deploy Firebase-first + Cloud Run (Thora)

## Arquitetura

| Camada | Serviço |
|--------|---------|
| **Frontend (SPA)** | Firebase Hosting — `https://borderless-5a4c8.web.app` |
| **Auth / Firestore / Storage** | Firebase projeto `borderless-5a4c8` |
| **API (PDF + IA)** | Cloud Run `thora-api` |

**Importante:** o front chama o Cloud Run **direto** (`VITE_API_URL`).  
Não use rewrite Hosting → Cloud Run para `/api`: o Hosting limita proxy a **60s** e jobs de PDF/IA passam disso.

O Netlify pode continuar como espelho legado; produção oficial é Firebase Hosting.

## Pré-requisitos

1. Plano **Blaze** no Firebase (`borderless-5a4c8`)
2. `gcloud` CLI (ou SDK em `.tools/google-cloud-sdk`)
3. `firebase` CLI (`npm install -g firebase-tools`)
4. Secrets no Secret Manager (recomendado):
   - `OPENAI_API_KEY`
   - `FIREBASE_CREDENTIALS` (JSON da service account, se não usar ADC)

```bash
printf '%s' "$OPENAI_API_KEY" | gcloud secrets create OPENAI_API_KEY --data-file=- --project=borderless-5a4c8
printf '%s' "$FIREBASE_CREDENTIALS" | gcloud secrets create FIREBASE_CREDENTIALS --data-file=- --project=borderless-5a4c8
```

## Deploy completo

```bash
cd /path/to/410---thora-construcao
chmod +x scripts/deploy-all-production.sh
./scripts/deploy-all-production.sh
```

O script:

1. Atualiza `main`
2. Faz deploy do Cloud Run (CORS já inclui `*.web.app` / `*.firebaseapp.com`)
3. Deploy das rules Firestore + Storage
4. Build do front com `VITE_API_URL=<Cloud Run>` e `firebase deploy --only hosting`

## Deploy só da API

```bash
./scripts/deploy-cloud-run.sh
```

| Env | Default |
|-----|---------|
| `CLOUD_RUN_MEMORY` | `2Gi` |
| `CLOUD_RUN_MAX_INSTANCES` | `1` (evita poll de detect-tables em outra réplica sem estado) |
| `CLOUD_RUN_REGION` | `us-central1` |

URL atual da API: `https://thora-api-333573409559.us-central1.run.app`

## Detecção de tabelas (Cloud Run)

Status de `detect-tables` e cache de candidatos **não** ficam só em `/tmp`:

- progresso do job → Firestore `detect_jobs/{uploadId}`
- cache com `rows` → Storage `table_caches/{uploadId}_tables.json`

Assim o poll de status funciona mesmo com mais de uma instância. Default de deploy: `max-instances=1`.


Firebase Console → Authentication → Settings → Authorized domains — garanta:

- `borderless-5a4c8.web.app`
- `borderless-5a4c8.firebaseapp.com`

(Normalmente já vêm no projeto Hosting.)

## Smoke test

```bash
curl -s https://thora-api-333573409559.us-central1.run.app/health
# → {"status":"ok","service":"thora-api","version":"2.0.0"}
```

No app (`https://borderless-5a4c8.web.app`): login → upload PDF → detectar tabelas → processar → validação → Curva ABC.

## Custos (Blaze)

- Cloud Run min=1: ~US$ 10–30/mês (sempre quente)
- Hosting / Firestore / Storage: baixo no volume atual
- OpenAI: conforme uso
