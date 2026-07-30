import { getApiBaseUrl, wakeApiServer } from "./api";

const DEFAULT_INTERVAL_MS = 5 * 60 * 1000;

function parseIntervalMs(): number {
  const raw = import.meta.env.VITE_KEEP_ALIVE_INTERVAL_MS;
  if (!raw) return DEFAULT_INTERVAL_MS;
  const parsed = Number(raw);
  return Number.isFinite(parsed) && parsed >= 60_000 ? parsed : DEFAULT_INTERVAL_MS;
}

/** Ativo só para backends que dormem (Render Free). Cloud Run com min=1 não precisa. */
export function shouldEnableBackendKeepAlive(apiBase = getApiBaseUrl()): boolean {
  const flag = String(import.meta.env.VITE_KEEP_ALIVE_ENABLED ?? "").toLowerCase();
  if (flag === "false" || flag === "0") return false;
  if (flag === "true" || flag === "1") return true;
  // Não martelar *.run.app — min-instances=1 e pings extras geram 429.
  return /\.onrender\.com/i.test(apiBase);
}

/**
 * Pings leves para backends que dormem (Render Free).
 * Evita axios em /health no intervalo — durante 502 o proxy não manda CORS e polui o console.
 */
export function startBackendKeepAlive(options?: {
  intervalMs?: number;
  apiBase?: string;
}): () => void {
  const apiBase = options?.apiBase ?? getApiBaseUrl();

  if (!shouldEnableBackendKeepAlive(apiBase)) {
    return () => {};
  }

  const intervalMs = options?.intervalMs ?? parseIntervalMs();

  const tick = () => {
    wakeApiServer();
  };

  tick();
  const timerId = window.setInterval(tick, intervalMs);

  if (import.meta.env.DEV) {
    console.info(
      `[keep-alive] wake a cada ${Math.round(intervalMs / 1000)}s → ${apiBase}/health`,
    );
  }

  return () => {
    window.clearInterval(timerId);
  };
}
