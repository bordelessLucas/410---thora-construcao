import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  CheckCircle2,
  Clock,
  Cpu,
  Download,
  Edit,
  Upload,
} from "lucide-react";
import { useAuth } from "../../features/auth/AuthContext";
import { listAuditLogsByUserId } from "../../features/dashboard/auditLogRepository";
import type { Orcamento } from "../../features/orcamentos/orcamentoTypes";
import {
  formatRelativeTime,
  getOrcamentoCreatedAt,
  getOrcamentoDisplayName,
  truncateLabel,
} from "./dashboardUtils";

type ActivityType = "upload" | "export" | "revision" | "processing" | "completion";

interface ActivityEvent {
  id: string;
  type: ActivityType;
  uploadId: string;
  orcamentoName: string;
  label: string;
  date: Date;
}

interface AtividadeRecenteProps {
  orcamentos: Orcamento[];
  loading: boolean;
}

const TYPE_CONFIG: Record<
  ActivityType,
  { icon: React.FC<{ className?: string }>; color: string }
> = {
  upload: { icon: Upload, color: "bg-blue-100 text-blue-600" },
  export: { icon: Download, color: "bg-violet-100 text-violet-600" },
  revision: { icon: Edit, color: "bg-amber-100 text-amber-600" },
  processing: { icon: Cpu, color: "bg-slate-100 text-slate-600" },
  completion: { icon: CheckCircle2, color: "bg-emerald-100 text-emerald-600" },
};

function buildFallbackEvents(orcamentos: Orcamento[]): ActivityEvent[] {
  const events: ActivityEvent[] = [];

  for (const o of orcamentos) {
    const name = getOrcamentoDisplayName(o, 36);
    const uploadedAt = getOrcamentoCreatedAt(o);
    const updatedAt = o.updatedAt ?? o.extractedAt;

    events.push({
      id: `upload-${o.uploadId}`,
      type: "upload",
      uploadId: o.uploadId,
      orcamentoName: name,
      label: "Orçamento enviado",
      date: uploadedAt,
    });

    if (o.extractedAt && o.status === "processing") {
      events.push({
        id: `processing-${o.uploadId}`,
        type: "processing",
        uploadId: o.uploadId,
        orcamentoName: name,
        label: "Processamento iniciado",
        date: o.extractedAt,
      });
    }

    if (o.status === "completed") {
      const completionDate = updatedAt ?? o.extractedAt ?? uploadedAt;
      events.push({
        id: `completion-${o.uploadId}`,
        type: "completion",
        uploadId: o.uploadId,
        orcamentoName: name,
        label: "Orçamento concluído",
        date: completionDate,
      });
    }

    if (
      updatedAt &&
      updatedAt.getTime() - uploadedAt.getTime() > 60_000 &&
      o.status === "completed"
    ) {
      events.push({
        id: `revision-${o.uploadId}`,
        type: "revision",
        uploadId: o.uploadId,
        orcamentoName: name,
        label: "Itens revisados",
        date: updatedAt,
      });
    }
  }

  return events
    .sort((a, b) => b.date.getTime() - a.date.getTime())
    .slice(0, 8);
}

function mapAuditToEvents(
  logs: Awaited<ReturnType<typeof listAuditLogsByUserId>>,
  orcamentos: Orcamento[],
): ActivityEvent[] {
  return logs.map((log) => {
    const orc = orcamentos.find((o) => o.uploadId === log.projectId);
    const name = orc
      ? getOrcamentoDisplayName(orc, 36)
      : truncateLabel(String(log.projectId), 36);
    const campo = log.campoAlterado.replace(/_/g, " ");
    return {
      id: `audit-${log.id}`,
      type: "revision" as const,
      uploadId: log.projectId,
      orcamentoName: name,
      label: `Item ${log.itemCodigo}: ${campo}`,
      date: log.timestamp,
    };
  });
}

const AtividadeRecente: React.FC<AtividadeRecenteProps> = ({
  orcamentos,
  loading: parentLoading,
}) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [loadingAudit, setLoadingAudit] = useState(false);

  const fallbackEvents = useMemo(
    () => buildFallbackEvents(orcamentos),
    [orcamentos],
  );

  useEffect(() => {
    if (!user?.uid) return;

    let cancelled = false;
    const load = async () => {
      setLoadingAudit(true);
      try {
        const logs = await listAuditLogsByUserId(user.uid, 8);
        if (cancelled) return;
        if (logs.length > 0) {
          setEvents(mapAuditToEvents(logs, orcamentos));
        } else {
          setEvents(fallbackEvents);
        }
      } catch {
        if (!cancelled) {
          setEvents(fallbackEvents);
        }
      } finally {
        if (!cancelled) setLoadingAudit(false);
      }
    };

    void load();
    return () => {
      cancelled = true;
    };
  }, [user?.uid, orcamentos, fallbackEvents]);

  const loading = parentLoading || loadingAudit;
  const displayEvents = events.length > 0 ? events.slice(0, 8) : fallbackEvents;

  return (
    <div className="surface-panel flex h-full min-h-[320px] flex-col p-5 sm:p-6">
      <h2 className="mb-4 font-display text-lg font-semibold text-slate-900">
        Atividade recente
      </h2>

      {loading ? (
        <div className="flex flex-col gap-4">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="flex gap-3">
              <div className="h-10 w-10 animate-pulse rounded-full bg-slate-200" />
              <div className="flex-1 space-y-2">
                <div className="h-4 w-3/4 animate-pulse rounded bg-slate-200" />
                <div className="h-3 w-1/3 animate-pulse rounded bg-slate-100" />
              </div>
            </div>
          ))}
        </div>
      ) : displayEvents.length === 0 ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-12 text-center">
          <Clock className="h-10 w-10 text-slate-300" />
          <p className="text-sm text-slate-500">Nenhuma atividade recente</p>
        </div>
      ) : (
        <ol className="relative flex flex-col gap-0">
          {displayEvents.map((event, idx) => {
            const config = TYPE_CONFIG[event.type];
            const Icon = config.icon;
            const isLast = idx === displayEvents.length - 1;
            return (
              <li key={event.id} className="relative flex gap-3 pb-6">
                {!isLast && (
                  <span
                    className="absolute left-5 top-10 h-[calc(100%-1.5rem)] w-px bg-slate-200"
                    aria-hidden
                  />
                )}
                <div
                  className={`relative z-10 flex h-10 w-10 shrink-0 items-center justify-center rounded-full ${config.color}`}
                >
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0 flex-1 pt-0.5 pr-1">
                  <button
                    type="button"
                    onClick={() => navigate(`/validacao/${event.uploadId}`)}
                    className="block w-full text-left"
                    title={event.orcamentoName}
                  >
                    <span className="block text-sm font-semibold text-slate-800 hover:text-thora-steel">
                      {event.label}
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-slate-500">
                      {event.orcamentoName}
                    </span>
                  </button>
                  <p className="mt-1 text-xs text-slate-400">
                    {formatRelativeTime(event.date)}
                  </p>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
};

export default AtividadeRecente;
