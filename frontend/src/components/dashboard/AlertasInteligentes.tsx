import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  AlertTriangle,
  CheckCircle,
  ChevronDown,
  Info,
  X,
} from "lucide-react";
import type { Orcamento } from "../../features/orcamentos/orcamentoTypes";
import {
  countItemsWithoutPrice,
  getOrcamentoCreatedAt,
  getOrcamentoDisplayName,
  isStaleProcessingStatus,
} from "./dashboardUtils";

const DISMISSED_KEY = "thora-dashboard-dismissed-alerts";
const MAX_VISIBLE = 4;

type AlertType = "warning" | "info" | "success";

interface DashboardAlert {
  id: string;
  type: AlertType;
  message: string;
  actionLabel?: string;
  actionPath?: string;
}

interface AlertasInteligentesProps {
  orcamentos: Orcamento[];
  loading: boolean;
}

function loadDismissed(): Set<string> {
  try {
    const raw = localStorage.getItem(DISMISSED_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as string[];
    return new Set(parsed);
  } catch {
    return new Set();
  }
}

function saveDismissed(ids: Set<string>): void {
  localStorage.setItem(DISMISSED_KEY, JSON.stringify([...ids]));
}

const AlertasInteligentes: React.FC<AlertasInteligentesProps> = ({
  orcamentos,
  loading,
}) => {
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState<Set<string>>(loadDismissed);
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    setDismissed(loadDismissed());
  }, []);

  const dismiss = useCallback((id: string) => {
    setDismissed((prev) => {
      const next = new Set(prev);
      next.add(id);
      saveDismissed(next);
      return next;
    });
  }, []);

  const allAlerts = useMemo((): DashboardAlert[] => {
    const alerts: DashboardAlert[] = [];

    for (const o of orcamentos) {
      if (isStaleProcessingStatus(o)) {
        const name = getOrcamentoDisplayName(o);
        const created = getOrcamentoCreatedAt(o).toLocaleDateString("pt-BR");
        alerts.push({
          id: `stale-${o.uploadId}`,
          type: "warning",
          message: `"${name}" está em processamento desde ${created} (mais de 48h).`,
          actionLabel: "Ver orçamento",
          actionPath: `/validacao/${o.uploadId}`,
        });
      }
    }

    const withoutPrice = orcamentos.filter((o) => countItemsWithoutPrice(o) > 0);
    if (withoutPrice.length > 0) {
      const totalItems = withoutPrice.reduce(
        (sum, o) => sum + countItemsWithoutPrice(o),
        0,
      );
      const first = withoutPrice[0];
      alerts.push({
        id: `no-price-${first.uploadId}`,
        type: "info",
        message: `${totalItems} item${totalItems > 1 ? "s" : ""} sem preço em ${withoutPrice.length} orçamento${withoutPrice.length > 1 ? "s" : ""}.`,
        actionLabel: "Revisar itens",
        actionPath: `/validacao/${first.uploadId}`,
      });
    }

    if (alerts.length === 0) {
      alerts.push({
        id: "all-clear",
        type: "success",
        message: "Tudo certo! Seus orçamentos estão em dia",
      });
    }

    return alerts;
  }, [orcamentos]);

  const visibleAlerts = useMemo(
    () => allAlerts.filter((a) => !dismissed.has(a.id)),
    [allAlerts, dismissed],
  );

  const shown = expanded ? visibleAlerts : visibleAlerts.slice(0, MAX_VISIBLE);
  const hiddenCount = Math.max(0, visibleAlerts.length - MAX_VISIBLE);

  const typeStyles: Record<AlertType, { bg: string; icon: string; border: string }> = {
    warning: {
      bg: "bg-amber-50",
      icon: "text-amber-600",
      border: "border-amber-100",
    },
    info: {
      bg: "bg-blue-50",
      icon: "text-blue-600",
      border: "border-blue-100",
    },
    success: {
      bg: "bg-emerald-50",
      icon: "text-emerald-600",
      border: "border-emerald-100",
    },
  };

  const TypeIcon: Record<AlertType, React.FC<{ className?: string }>> = {
    warning: AlertTriangle,
    info: Info,
    success: CheckCircle,
  };

  return (
    <div className="surface-panel flex h-full min-h-[320px] flex-col p-5 sm:p-6">
      <h2 className="mb-4 font-display text-lg font-semibold text-slate-900">Alertas</h2>

      {loading ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl bg-slate-100" />
          ))}
        </div>
      ) : visibleAlerts.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-8 text-center">
          <CheckCircle className="h-10 w-10 text-emerald-400" />
          <p className="text-sm text-slate-500">Nenhum alerta pendente</p>
        </div>
      ) : (
        <div className="flex flex-1 flex-col gap-3">
          {shown.map((alert) => {
            const styles = typeStyles[alert.type];
            const Icon = TypeIcon[alert.type];
            return (
              <div
                key={alert.id}
                className={`relative rounded-xl border p-4 ${styles.bg} ${styles.border}`}
              >
                {alert.type !== "success" && (
                  <button
                    type="button"
                    onClick={() => dismiss(alert.id)}
                    className="absolute right-2 top-2 rounded-lg p-1 text-slate-400 transition hover:bg-white/60 hover:text-slate-600"
                    aria-label="Dispensar alerta"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
                <div className="flex gap-3 pr-6">
                  <Icon className={`mt-0.5 h-5 w-5 shrink-0 ${styles.icon}`} />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm text-slate-800">{alert.message}</p>
                    {alert.actionLabel && alert.actionPath && (
                      <button
                        type="button"
                        onClick={() => navigate(alert.actionPath!)}
                        className="mt-2 text-xs font-medium text-thora-steel hover:underline"
                      >
                        {alert.actionLabel}
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          {!expanded && hiddenCount > 0 && (
            <button
              type="button"
              onClick={() => setExpanded(true)}
              className="inline-flex items-center justify-center gap-1 text-sm font-medium text-slate-600 hover:text-slate-900"
            >
              <ChevronDown className="h-4 w-4" />
              Ver mais {hiddenCount} alerta{hiddenCount > 1 ? "s" : ""}
            </button>
          )}
        </div>
      )}
    </div>
  );
};

export default AlertasInteligentes;
