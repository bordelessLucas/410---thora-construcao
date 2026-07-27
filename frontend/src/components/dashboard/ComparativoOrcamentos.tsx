import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ChevronDown, GitCompareArrows, Loader2 } from "lucide-react";
import { useAuth } from "../../features/auth/AuthContext";
import { getOrcamentoByUploadId } from "../../features/orcamentos/orcamentoRepository";
import type { Orcamento } from "../../features/orcamentos/orcamentoTypes";
import { getOrcamentoTotal } from "../../features/orcamentos/orcamentoAnalytics";
import {
  countClassAItems,
  formatCurrency,
  getOrcamentoCreatedAt,
  getOrcamentoDisplayName,
} from "./dashboardUtils";

interface ComparativoOrcamentosProps {
  orcamentos: Orcamento[];
  loading: boolean;
}

type ComparativoRow = {
  uploadId: string;
  nome: string;
  valorTotal: number;
  qtdItens: number;
  itensClasseA: number;
  data: Date;
};

const ComparativoOrcamentos: React.FC<ComparativoOrcamentosProps> = ({
  orcamentos,
  loading: parentLoading,
}) => {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [collapsed, setCollapsed] = useState(false);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [detailed, setDetailed] = useState<Orcamento[]>([]);
  const dropdownRef = useRef<HTMLDivElement>(null);

  const completedOrcamentos = useMemo(
    () => orcamentos.filter((o) => o.status === "completed"),
    [orcamentos],
  );

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const toggleSelection = (uploadId: string) => {
    setSelectedIds((prev) => {
      if (prev.includes(uploadId)) {
        return prev.filter((id) => id !== uploadId);
      }
      if (prev.length >= 3) return prev;
      return [...prev, uploadId];
    });
  };

  const loadDetails = useCallback(async () => {
    if (!user?.uid || selectedIds.length < 2) {
      setDetailed([]);
      return;
    }

    setFetching(true);
    setFetchError(null);
    try {
      const results: Orcamento[] = [];
      for (const uploadId of selectedIds) {
        const cached = orcamentos.find((o) => o.uploadId === uploadId);
        if (cached?.items?.length) {
          results.push(cached);
          continue;
        }
        const fresh = await getOrcamentoByUploadId(user.uid, uploadId);
        if (fresh) results.push(fresh);
      }
      setDetailed(results);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Erro ao carregar orçamentos.";
      setFetchError(msg);
      setDetailed([]);
    } finally {
      setFetching(false);
    }
  }, [user?.uid, selectedIds, orcamentos]);

  useEffect(() => {
    void loadDetails();
  }, [loadDetails]);

  const rows = useMemo((): ComparativoRow[] => {
    return detailed.map((o) => ({
      uploadId: o.uploadId,
      nome: getOrcamentoDisplayName(o),
      valorTotal: getOrcamentoTotal(o),
      qtdItens: o.itemsFound ?? o.items?.length ?? 0,
      itensClasseA: countClassAItems(o),
      data: getOrcamentoCreatedAt(o),
    }));
  }, [detailed]);

  const insight = useMemo(() => {
    if (rows.length < 2) return null;
    const base = rows[0].valorTotal;
    const others = rows.slice(1);
    const avgOthers =
      others.reduce((sum, r) => sum + r.valorTotal, 0) / others.length;
    if (avgOthers <= 0) return null;
    const diffPct = ((base - avgOthers) / avgOthers) * 100;
    const absPct = Math.abs(diffPct).toFixed(1);
    if (Math.abs(diffPct) < 0.5) {
      return `"${rows[0].nome}" está alinhado com a média dos selecionados.`;
    }
    const direction = diffPct > 0 ? "acima" : "abaixo";
    return `"${rows[0].nome}" está ${absPct}% ${direction} da média dos selecionados.`;
  }, [rows]);

  const loading = parentLoading || fetching;
  const showComparison = selectedIds.length >= 2 && !loading && rows.length >= 2;

  return (
    <div className="surface-panel overflow-hidden">
      <button
        type="button"
        onClick={() => setCollapsed((v) => !v)}
        className="flex w-full items-center justify-between gap-3 border-b border-slate-200 px-4 py-4 text-left sm:px-6"
      >
        <div className="flex items-center gap-2">
          <GitCompareArrows className="h-5 w-5 text-violet-600" />
          <div>
            <h2 className="font-display text-lg font-semibold text-slate-900">
              Comparativo entre orçamentos
            </h2>
            <p className="text-sm text-slate-500">
              Selecione 2 ou 3 orçamentos para comparar
            </p>
          </div>
        </div>
        <ChevronDown
          className={`h-5 w-5 shrink-0 text-slate-400 transition ${collapsed ? "" : "rotate-180"}`}
        />
      </button>

      {!collapsed && (
        <div className="space-y-6 p-4 sm:p-6">
          {completedOrcamentos.length === 0 ? (
            <div className="rounded-xl border border-dashed border-slate-200 bg-slate-50/70 px-4 py-8 text-center">
              <p className="text-sm font-medium text-slate-700">
                Nenhum orçamento finalizado ainda
              </p>
              <p className="mt-1 text-xs text-slate-500">
                Conclua ao menos 2 análises para liberar o comparativo.
              </p>
            </div>
          ) : (
            <div ref={dropdownRef} className="relative max-w-xl">
              <button
                type="button"
                disabled={parentLoading}
                onClick={() => setDropdownOpen((v) => !v)}
                className="flex w-full items-center justify-between rounded-xl border border-slate-200 bg-white px-4 py-3 text-left text-sm text-slate-700 transition hover:border-slate-300 disabled:opacity-50"
              >
                <span className="truncate">
                  {selectedIds.length === 0
                    ? "Selecione orçamentos…"
                    : `${selectedIds.length} selecionado${selectedIds.length > 1 ? "s" : ""}`}
                </span>
                <ChevronDown className="h-4 w-4 shrink-0 text-slate-400" />
              </button>

              {dropdownOpen && (
                <div className="absolute z-20 mt-2 max-h-64 w-full overflow-y-auto rounded-xl border border-slate-200 bg-white py-2 shadow-lg">
                  {completedOrcamentos.map((o) => {
                    const checked = selectedIds.includes(o.uploadId);
                    const disabled = !checked && selectedIds.length >= 3;
                    return (
                      <label
                        key={o.uploadId}
                        className={`flex cursor-pointer items-center gap-3 px-4 py-2.5 text-sm hover:bg-slate-50 ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          disabled={disabled}
                          onChange={() => toggleSelection(o.uploadId)}
                          className="h-4 w-4 rounded border-slate-300 text-thora-steel focus:ring-thora-sky"
                        />
                        <span
                          className="min-w-0 flex-1 truncate text-slate-800"
                          title={getOrcamentoDisplayName(o)}
                        >
                          {getOrcamentoDisplayName(o, 48)}
                        </span>
                        <span className="shrink-0 text-xs tabular-nums text-slate-500">
                          {formatCurrency(getOrcamentoTotal(o))}
                        </span>
                      </label>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {fetchError && (
            <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
              {fetchError}
            </div>
          )}

          {loading && selectedIds.length >= 2 && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="h-4 w-4 animate-spin" />
              Carregando dados para comparação…
            </div>
          )}

          {showComparison && (
            <>
              {insight && (
                <div className="rounded-xl border border-violet-100 bg-violet-50 px-4 py-3 text-sm text-violet-900">
                  {insight}
                </div>
              )}

              <div className="overflow-x-auto">
                <table className="w-full min-w-[640px] text-sm">
                  <thead className="bg-slate-50 text-left text-slate-500">
                    <tr>
                      <th className="px-4 py-3">Orçamento</th>
                      <th className="px-4 py-3 text-right">Valor total</th>
                      <th className="px-4 py-3 text-right">Qtd itens</th>
                      <th className="px-4 py-3 text-right">Itens classe A</th>
                      <th className="px-4 py-3 text-right">Data</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-200">
                    {rows.map((row, idx) => {
                      const base = rows[0]?.valorTotal ?? 0;
                      const diffPct =
                        idx > 0 && base > 0
                          ? ((row.valorTotal - base) / base) * 100
                          : null;
                      const diffColor =
                        diffPct === null
                          ? ""
                          : diffPct < 0
                            ? "text-emerald-600"
                            : diffPct > 0
                              ? "text-red-600"
                              : "text-slate-600";

                      return (
                        <tr key={row.uploadId} className="hover:bg-slate-50/80">
                          <td className="px-4 py-3">
                            <button
                              type="button"
                              onClick={() => navigate(`/validacao/${row.uploadId}`)}
                              className="font-medium text-blue-600 hover:underline"
                            >
                              {row.nome}
                            </button>
                          </td>
                          <td className="px-4 py-3 text-right">
                            <p className="font-medium tabular-nums">
                              {formatCurrency(row.valorTotal)}
                            </p>
                            {diffPct !== null && (
                              <p className={`text-xs tabular-nums ${diffColor}`}>
                                {diffPct > 0 ? "+" : ""}
                                {diffPct.toFixed(1)}% vs. 1º
                              </p>
                            )}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {row.qtdItens}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {row.itensClasseA}
                          </td>
                          <td className="px-4 py-3 text-right text-slate-600">
                            {row.data.toLocaleDateString("pt-BR")}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {!loading && selectedIds.length === 1 && (
            <p className="text-sm text-slate-500">
              Selecione pelo menos mais um orçamento para comparar.
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default ComparativoOrcamentos;
