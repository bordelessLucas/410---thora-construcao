import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { RefreshCw } from "lucide-react";
import { useAuth } from "../features/auth/AuthContext";
import { listOrcamentosByUserId } from "../features/orcamentos/orcamentoRepository";
import type { Orcamento } from "../features/orcamentos/orcamentoTypes";
import {
  formatCurrency,
  getOrcamentoTotal,
} from "../features/orcamentos/orcamentoAnalytics";
import { getOrcamentoDisplayName } from "../components/dashboard/dashboardUtils";
import OrcamentoAnalyticsCharts from "../components/OrcamentoAnalyticsCharts";
import TendenciaTemporalChart from "../components/dashboard/TendenciaTemporalChart";
import AlertasInteligentes from "../components/dashboard/AlertasInteligentes";
import AtividadeRecente from "../components/dashboard/AtividadeRecente";
import ComparativoOrcamentos from "../components/dashboard/ComparativoOrcamentos";
import { btnPrimary, btnSecondary } from "../components/ui/buttonClasses";

interface ResumoCardProps {
  titulo: string;
  valor: string;
  descricao: string;
  extra?: string;
  variant: "blue" | "gray" | "yellow" | "green";
  /** Classe extra no valor (ex.: moeda longa precisa de fonte menor) */
  valorClassName?: string;
}

const variantStyles = {
  blue: "border-thora-steel/15 bg-white/90 text-thora-steel",
  gray: "border-slate-200/80 bg-white/90 text-slate-800",
  yellow: "border-amber-200/80 bg-white/90 text-amber-700",
  green: "border-teal-200/70 bg-white/90 text-thora-accent",
};

const ResumoCard: React.FC<ResumoCardProps> = ({
  titulo,
  valor,
  descricao,
  extra,
  variant,
  valorClassName,
}) => {
  const valorSize =
    valorClassName ??
    (valor.length > 12
      ? "text-xl sm:text-2xl"
      : "text-3xl sm:text-4xl");

  return (
    <div
      className={`surface-panel flex min-w-0 flex-col gap-2 border p-5 sm:p-6 ${variantStyles[variant]}`}
    >
      <p className="text-sm font-medium text-slate-500">{titulo}</p>
      <p
        className={`font-display font-bold tabular-nums leading-tight tracking-tight break-words ${valorSize}`}
        title={valor}
      >
        {valor}
      </p>
      <p className="text-sm text-slate-600">{descricao}</p>
      {extra && <p className="text-sm font-medium text-thora-accent">{extra}</p>}
    </div>
  );
};

const Dashboard: React.FC = () => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [orcamentos, setOrcamentos] = useState<Orcamento[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  const fetchOrcamentos = useCallback(async () => {
    if (!user?.uid) return;
    setLoading(true);
    setLoadError(null);
    try {
      const data = await listOrcamentosByUserId(user.uid);
      setOrcamentos(data);
    } catch (e: unknown) {
      const msg =
        e instanceof Error ? e.message : "Não foi possível carregar os orçamentos.";
      console.error("[Dashboard] Falha ao carregar orçamentos:", e);
      setLoadError(msg);
      toast.error("Falha ao carregar dados", { description: msg });
    } finally {
      setLoading(false);
    }
  }, [user?.uid]);

  useEffect(() => {
    void fetchOrcamentos();
  }, [fetchOrcamentos]);

  const stats = useMemo(() => {
    const total = orcamentos.length;
    const processing = orcamentos.filter((o) => o.status === "processing").length;
    const completed = orcamentos.filter((o) => o.status === "completed");
    const error = orcamentos.filter((o) => o.status === "error").length;
    const valorExportado = completed.reduce((s, o) => s + getOrcamentoTotal(o), 0);
    return {
      total,
      processing,
      completed: completed.length,
      error,
      valorExportado,
    };
  }, [orcamentos]);

  return (
    <div className="flex-1 overflow-auto">
      <div className="mx-auto w-full max-w-7xl px-4 py-6 sm:px-6 sm:py-8">
        <div className="mb-8 flex flex-col gap-4 sm:mb-10 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <h1 className="page-title">Dashboard</h1>
            <p className="page-subtitle">
              Visão geral dos orçamentos analisados e exportados
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void fetchOrcamentos()}
              disabled={loading}
              className={`${btnSecondary} shrink-0`}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Atualizar
            </button>
            <button
              type="button"
              onClick={() => navigate("/analise-orcamento")}
              className={btnPrimary}
            >
              Nova análise
            </button>
          </div>
        </div>

        {loadError && !loading && (
          <div className="mb-6 rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
            {loadError}
          </div>
        )}

        <div className="mb-10 grid grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-6 lg:grid-cols-4 [&>*]:min-w-0">
          <ResumoCard
            titulo="Total de Orçamentos"
            valor={loading ? "—" : String(stats.total)}
            descricao="Todos os projetos"
            variant="blue"
          />
          <ResumoCard
            titulo="Em processamento"
            valor={loading ? "—" : String(stats.processing)}
            descricao="Extração ou análise em andamento"
            variant="gray"
          />
          <ResumoCard
            titulo="Analisados / exportados"
            valor={loading ? "—" : String(stats.completed)}
            descricao="Prontos para validação e relatórios"
            variant="green"
          />
          <ResumoCard
            titulo="Valor consolidado"
            valor={loading ? "—" : formatCurrency(stats.valorExportado)}
            descricao="Soma dos orçamentos finalizados"
            variant="yellow"
            valorClassName="text-base sm:text-lg lg:text-xl"
          />
        </div>

        {/* Linha 2: Tendência + Alertas */}
        <div className="mb-8 grid grid-cols-1 gap-6 lg:grid-cols-3">
          <div className="lg:col-span-2">
            <TendenciaTemporalChart orcamentos={orcamentos} loading={loading} />
          </div>
          <div className="lg:col-span-1">
            <AlertasInteligentes orcamentos={orcamentos} loading={loading} />
          </div>
        </div>

        {/* Analytics em largura total — evita esmagar labels/gráficos */}
        <div className="mb-8">
          <OrcamentoAnalyticsCharts
            orcamentos={orcamentos}
            loading={loading}
            onRefresh={() => void fetchOrcamentos()}
            sectionClassName="mt-0"
          />
        </div>

        <div className="mb-8 grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div className="xl:col-span-2">
            <ComparativoOrcamentos orcamentos={orcamentos} loading={loading} />
          </div>
          <AtividadeRecente orcamentos={orcamentos} loading={loading} />
        </div>

        <div className="surface-panel overflow-hidden">
          <div className="flex flex-col gap-3 border-b border-slate-200/80 px-4 py-4 sm:flex-row sm:items-center sm:justify-between sm:px-6 sm:py-5">
            <h2 className="font-display text-lg font-semibold text-slate-900">
              Orçamentos recentes
            </h2>
            <button
              type="button"
              onClick={() => navigate("/analise-orcamento")}
              className={`${btnPrimary} w-full sm:w-auto`}
            >
              Nova análise ABC
            </button>
          </div>

          <div className="overflow-x-auto">
            <table className="w-full table-fixed text-sm">
              <colgroup>
                <col className="w-[38%]" />
                <col className="w-[16%]" />
                <col className="w-[18%]" />
                <col className="w-[10%]" />
                <col className="w-[18%]" />
              </colgroup>
              <thead className="bg-slate-50/90 text-left text-slate-500">
                <tr>
                  <th className="px-4 py-3 font-medium sm:px-6 sm:py-4">Obra / Projeto</th>
                  <th className="px-4 py-3 font-medium sm:px-6 sm:py-4">Status</th>
                  <th className="px-4 py-3 text-right font-medium sm:px-6 sm:py-4">Valor total</th>
                  <th className="px-4 py-3 text-right font-medium sm:px-6 sm:py-4">Itens</th>
                  <th className="px-4 py-3 text-right font-medium sm:px-6 sm:py-4">Ações</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-200">
                {loading ? (
                  <tr>
                    <td className="px-4 py-8 text-slate-500 sm:px-6" colSpan={5}>
                      Carregando orçamentos…
                    </td>
                  </tr>
                ) : loadError ? (
                  <tr>
                    <td className="px-4 py-8 sm:px-6" colSpan={5}>
                      <p className="text-slate-600">Não foi possível carregar a lista.</p>
                      <button
                        type="button"
                        onClick={() => void fetchOrcamentos()}
                        className={`${btnSecondary} mt-3`}
                      >
                        Tentar novamente
                      </button>
                    </td>
                  </tr>
                ) : orcamentos.length === 0 ? (
                  <tr>
                    <td className="px-4 py-8 text-slate-500 sm:px-6" colSpan={5}>
                      Nenhum orçamento encontrado. Use{" "}
                      <button
                        type="button"
                        className="font-medium text-thora-steel underline-offset-2 hover:underline"
                        onClick={() => navigate("/analise-orcamento")}
                      >
                        Nova análise
                      </button>{" "}
                      para começar.
                    </td>
                  </tr>
                ) : (
                  orcamentos.slice(0, 20).map((o) => {
                    const statusLabel =
                      o.status === "completed"
                        ? "Finalizado"
                        : o.status === "processing"
                          ? "Em processamento"
                          : "Erro";

                    const statusPill =
                      o.status === "completed"
                        ? "bg-emerald-100 text-emerald-800"
                        : o.status === "processing"
                          ? "bg-sky-100 text-sky-800"
                          : "bg-red-100 text-red-800";

                    const displayName = getOrcamentoDisplayName(o);
                    const valor =
                      o.status === "completed"
                        ? formatCurrency(getOrcamentoTotal(o))
                        : "—";

                    return (
                      <tr key={o.id} className="hover:bg-slate-50/80">
                        <td className="px-4 py-4 sm:px-6">
                          <p
                            className="truncate font-semibold text-slate-900"
                            title={o.filename || displayName}
                          >
                            {displayName}
                          </p>
                          <p className="mt-0.5 text-xs text-slate-500">
                            {o.uploadedAt.toLocaleDateString("pt-BR", {
                              day: "2-digit",
                              month: "short",
                              year: "numeric",
                            })}
                          </p>
                        </td>
                        <td className="px-4 py-4 sm:px-6">
                          <span
                            className={`inline-flex rounded-full px-3 py-1 text-xs font-medium ${statusPill}`}
                          >
                            {statusLabel}
                          </span>
                        </td>
                        <td className="px-4 py-4 text-right font-medium tabular-nums text-slate-900 sm:px-6">
                          {valor}
                        </td>
                        <td className="px-4 py-4 text-right tabular-nums text-slate-700 sm:px-6">
                          {o.itemsFound ?? "—"}
                        </td>
                        <td className="px-4 py-4 sm:px-6">
                          {o.status === "completed" ? (
                            <div className="flex flex-wrap items-center justify-end gap-x-3 gap-y-1">
                              <button
                                type="button"
                                className="text-xs font-semibold text-thora-steel hover:underline"
                                onClick={() => navigate(`/validacao/${o.uploadId}`)}
                              >
                                Validar
                              </button>
                              <button
                                type="button"
                                className="text-xs font-semibold text-teal-700 hover:underline"
                                onClick={() => navigate(`/curva-abc/${o.uploadId}`)}
                              >
                                Curva ABC
                              </button>
                            </div>
                          ) : (
                            <span className="block text-right text-xs text-slate-400">—</span>
                          )}
                        </td>
                      </tr>
                    );
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Dashboard;
