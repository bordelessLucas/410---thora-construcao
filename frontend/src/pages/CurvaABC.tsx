import React, { useState, useMemo, useEffect } from "react";
import { useParams, useNavigate, useLocation } from "react-router-dom";
import {
  ArrowLeft,
  TrendingUp,
  Package,
  AlertCircle,
  CheckCircle2,
  Download,
  ChevronRight,
  Loader2,
} from "lucide-react";
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
  Cell,
} from "recharts";
import { standardizeItemsWithAI, getCurvaABC, analyzeWithAI } from "../services/api";
import ExportModal from "../components/ExportModal";
import { CURVA_ABC_ONLY } from "../features/orcamentos/outputModels";
import { useAuth } from "../features/auth/AuthContext";
import { getLatestBDIAplicado } from "../features/bdi/bdiAplicadoRepository";
import type { BDIAplicado } from "../types/bdi";
import { toast } from "sonner";
import { parseBrl } from "../features/orcamentos/parseBrl";

interface Item {
  id: string;
  descricao: string;
  quantidade: number;
  unidade: string;
  valor_unitario: number;
  valor_total: number;
  status: "validado" | "pendente_validacao";
  classification?: "A" | "B" | "C";
  accumulated_percentage?: number;
}

type RawItem = Partial<Item> & {
  id?: string | number;
  description?: string;
  unit?: string;
  qty?: number;
  unitPrice?: number;
  lineTotal?: number;
  bdi?: number;
  valor_total_com_bdi?: number;
  valor_unitario_com_bdi?: number;
  quarentena?: boolean;
  abc_elegivel?: boolean;
};

const CurvaABC: React.FC = () => {
  const { uploadId } = useParams();
  const navigate = useNavigate();
  const location = useLocation();
  const [selectedFilter, setSelectedFilter] = useState<"all" | "A" | "B" | "C">(
    "all",
  );
  const [items, setItems] = useState<Item[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiResult, setAiResult] = useState<{
    provider?: string;
    totalItems?: number;
    valorTotal?: number;
    confianca?: number;
    warnings?: string[];
  } | null>(null);
  const [exportModalOpen, setExportModalOpen] = useState(false);
  const { user } = useAuth();
  const [bdiAplicado, setBdiAplicado] = useState<BDIAplicado | null>(null);

  const flatExportItems = useMemo(
    () =>
      items.map((item) => ({
        descricao: item.descricao,
        quantidade: item.quantidade,
        unidade: item.unidade,
        valor_unitario: item.valor_unitario,
        valor_total: item.valor_total,
        classification: item.classification,
      })),
    [items],
  );

  const toNumber = (value: unknown): number => parseBrl(value);

  const normalizeItem = (raw: RawItem, index: number): Item => {
    const quantidade = toNumber(raw.quantidade ?? raw.qty);
    const valorUnitario = toNumber(
      raw.valor_unitario_com_bdi ?? raw.valor_unitario ?? raw.unitPrice,
    );
    const bdi = toNumber(raw.bdi);
    let valorTotalExplicit = toNumber(
      raw.valor_total_com_bdi ?? raw.valor_total ?? raw.lineTotal,
    );
    // Correção: total colado na quantidade → Qtd×VU (custo parcial)
    if (
      quantidade > 1 &&
      valorUnitario > 0 &&
      valorTotalExplicit > 0 &&
      Math.abs(valorTotalExplicit - quantidade) <= Math.max(0.01, Math.abs(quantidade) * 1e-9)
    ) {
      const expected = quantidade * valorUnitario;
      if (Math.abs(expected - valorTotalExplicit) > Math.max(1, expected * 0.02)) {
        valorTotalExplicit = expected;
      }
    }
    const valorTotal =
      valorTotalExplicit > 0
        ? valorTotalExplicit
        : quantidade * valorUnitario * (1 + bdi / 100);

    const ineligible =
      raw.quarentena === true ||
      raw.abc_elegivel === false ||
      String((raw as { tipo_linha?: string; tipo?: string }).tipo_linha ?? (raw as { tipo?: string }).tipo ?? "")
        .toLowerCase() === "grupo";

    // Quarentena / inelegível / grupo: zera para não entrar no Pareto local
    if (ineligible || valorTotal <= 0) {
      return {
        id: String(raw.id ?? index + 1),
        descricao: String(raw.descricao ?? raw.description ?? "").trim(),
        quantidade,
        unidade: String(raw.unidade ?? raw.unit ?? "un").trim() || "un",
        valor_unitario: valorUnitario,
        valor_total: 0,
        status: (raw.status as Item["status"]) || "pendente_validacao",
        classification: undefined,
        accumulated_percentage: 0,
      };
    }

    return {
      id: String(raw.id ?? index + 1),
      descricao: String(raw.descricao ?? raw.description ?? "").trim(),
      quantidade,
      unidade: String(raw.unidade ?? raw.unit ?? "un").trim() || "un",
      valor_unitario: valorUnitario,
      valor_total: valorTotal,
      status: (raw.status as Item["status"]) || "validado",
      classification: raw.classification,
      accumulated_percentage: toNumber(raw.accumulated_percentage),
    };
  };

  /**
   * Curva ABC (Pareto 80/15/5 em valor): ordena por valor total decrescente,
   * acumula o % sobre o total do orçamento e classifica pelo % acumulado
   * *depois* de incluir o item (A ≤ 80%, B ≤ 95%, C > 95%).
   */
  const classifyItemsABC = (baseItems: Item[]): Item[] => {
    // Só linhas com valor (grupos/quarentena já vieram zerados)
    const eligible = baseItems.filter((item) => item.valor_total > 0);
    const sortedItems = [...eligible].sort((a, b) => {
      const diff = b.valor_total - a.valor_total;
      if (diff !== 0) return diff;
      return String(a.id).localeCompare(String(b.id), "pt-BR");
    });
    const total = sortedItems.reduce((sum, item) => sum + item.valor_total, 0);
    let accumulated = 0;

    return sortedItems.map((item) => {
      accumulated += item.valor_total;
      const accumulated_percentage = total > 0 ? (accumulated / total) * 100 : 0;

      let classification: "A" | "B" | "C" = "C";
      if (accumulated_percentage <= 80) {
        classification = "A";
      } else if (accumulated_percentage <= 95) {
        classification = "B";
      }

      return {
        ...item,
        classification,
        accumulated_percentage: Math.round(accumulated_percentage * 10) / 10,
      };
    });
  };

  // Buscar dados reais da Curva ABC
  useEffect(() => {
    const fetchCurvaABC = async () => {
      if (!uploadId) {
        setError("Upload ID não fornecido");
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setError(null);

        const editedItems = location.state?.editedItems as RawItem[] | undefined;
        
        // Verificar se há items selecionados passados via location.state
        const selectedItems = location.state?.items as RawItem[] | undefined;

        if (editedItems && editedItems.length > 0) {
          const normalizedEditedItems = editedItems.map((item, index) =>
            normalizeItem(item, index),
          );
          setItems(classifyItemsABC(normalizedEditedItems));
        } else if (selectedItems && selectedItems.length > 0) {
          // Usar apenas os items selecionados e calcular classificação ABC
          const normalizedSelectedItems = selectedItems.map((item, index) =>
            normalizeItem(item, index),
          );
          setItems(classifyItemsABC(normalizedSelectedItems));
        } else {
          // Buscar todos os items da API
          const response = await getCurvaABC(uploadId);
          const normalizedApiItems = ((response.items || []) as RawItem[]).map(
            (item, index) => normalizeItem(item, index),
          );
          setItems(classifyItemsABC(normalizedApiItems));
        }
      } catch (err: any) {
        console.error("Erro ao buscar Curva ABC:", err);
        setError(err.message || "Erro ao carregar dados da Curva ABC");
      } finally {
        setLoading(false);
      }
    };

    fetchCurvaABC();
  }, [uploadId, location.state]);

  useEffect(() => {
    if (!uploadId || !user?.uid) {
      setBdiAplicado(null);
      return;
    }
    void getLatestBDIAplicado(user.uid, uploadId).then(setBdiAplicado);
  }, [uploadId, user?.uid]);

  // Calcula resumo
  const summary = useMemo(() => {
    const total = items.reduce((sum, item) => sum + item.valor_total, 0);
    const countA = items.filter((i) => i.classification === "A").length;
    const countB = items.filter((i) => i.classification === "B").length;
    const countC = items.filter((i) => i.classification === "C").length;
    const valueA = items.filter((i) => i.classification === "A").reduce(
      (sum, item) => sum + item.valor_total,
      0,
    );
    const valueB = items.filter((i) => i.classification === "B").reduce(
      (sum, item) => sum + item.valor_total,
      0,
    );
    const valueC = items.filter((i) => i.classification === "C").reduce(
      (sum, item) => sum + item.valor_total,
      0,
    );

    return {
      total,
      countA,
      countB,
      countC,
      valueA,
      valueB,
      valueC,
      percentA: total > 0 ? ((valueA / total) * 100).toFixed(1) : 0,
      percentB: total > 0 ? ((valueB / total) * 100).toFixed(1) : 0,
      percentC: total > 0 ? ((valueC / total) * 100).toFixed(1) : 0,
    };
  }, [items]);

  // Filtra itens
  const filteredItems = useMemo(() => {
    if (selectedFilter === "all") return items;
    return items.filter((item) => item.classification === selectedFilter);
  }, [items, selectedFilter]);

  const handleAiStandardize = async () => {
    setAiLoading(true);
    setAiError(null);
    try {
      if (!uploadId) {
        throw new Error("Upload ID não fornecido para análise de IA");
      }

      const response = await analyzeWithAI(uploadId, "all");
      const aiItems = Array.isArray(response?.analysis?.items)
        ? (response.analysis.items as RawItem[])
        : [];
      let nextItems: Item[] = [...items];

      if (aiItems.length > 0) {
        const normalized = aiItems.map((item, index) => normalizeItem(item, index));
        nextItems = classifyItemsABC(normalized);
        setItems(nextItems);
      } else {
        const standardized = await standardizeItemsWithAI(items);
        if (Array.isArray(standardized.items)) {
          const normalized = (standardized.items as RawItem[]).map((item, index) =>
            normalizeItem(item, index),
          );
          nextItems = classifyItemsABC(normalized);
          setItems(nextItems);
        }
      }

      setAiResult({
        provider: response?.provider,
        totalItems: Number(response?.analysis?.summary?.total_items || 0),
        valorTotal: Number(response?.analysis?.summary?.valor_total || 0),
        confianca: Number(response?.analysis?.summary?.confianca_analise || 0),
        warnings: Array.isArray(response?.warnings) ? response.warnings : [],
      });

      toast.success("Itens padronizados com IA", {
        description: `${nextItems.length} itens atualizados na Curva ABC.`,
      });
    } catch (error: any) {
      setAiError(error.message || "Erro ao padronizar itens com IA");
    } finally {
      setAiLoading(false);
    }
  };

  // Dados para o gráfico
  const chartData = [
    {
      name: "Classe A",
      itens: summary.countA,
      valor: summary.valueA,
      fill: "#1F4E78",
    },
    {
      name: "Classe B",
      itens: summary.countB,
      valor: summary.valueB,
      fill: "#2E7AD4",
    },
    {
      name: "Classe C",
      itens: summary.countC,
      valor: summary.valueC,
      fill: "#9FC2E8",
    },
  ];

  const getClassificationColor = (classification?: string) => {
    switch (classification) {
      case "A":
        return "bg-red-50 border-red-200 text-red-700";
      case "B":
        return "bg-amber-50 border-amber-200 text-amber-700";
      case "C":
        return "bg-green-50 border-green-200 text-green-700";
      default:
        return "bg-slate-50 border-slate-200 text-slate-700";
    }
  };

  const getClassificationBadge = (classification?: string) => {
    const badges = {
      A: {
        color: "bg-red-50 text-red-800 border border-red-100",
        label: "Alto impacto",
        dot: "bg-red-600",
      },
      B: {
        color: "bg-amber-50 text-amber-800 border border-amber-100",
        label: "Médio impacto",
        dot: "bg-amber-500",
      },
      C: {
        color: "bg-emerald-50 text-emerald-800 border border-emerald-100",
        label: "Baixo impacto",
        dot: "bg-emerald-600",
      },
    };
    const badge = badges[classification as keyof typeof badges];
    return (
      badge || {
        color: "bg-slate-100 text-slate-700 border border-slate-200",
        label: "",
        dot: "bg-slate-400",
      }
    );
  };

  const handleBackToValidacao = () => {
    if (!uploadId) {
      navigate("/analise-orcamento");
      return;
    }
    // Preserva o state da sessão (itens / PDF ainda não persistidos) ao voltar
    navigate(`/validacao/${uploadId}`, {
      state: location.state ?? undefined,
    });
  };

  return (
    <div className="w-full min-h-full py-8 pb-16">
      <div className="mx-auto max-w-7xl px-4 sm:px-6">
        {/* Header */}
        <div className="mb-8 flex items-center gap-4">
          <button
            type="button"
            onClick={handleBackToValidacao}
            className="rounded-xl p-2 text-slate-600 transition hover:bg-white/80"
            aria-label="Voltar para validação"
          >
            <ArrowLeft size={24} aria-hidden />
          </button>
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-thora-steel">
              Thora
            </p>
            <h1 className="page-title text-3xl sm:text-3xl">Análise de Curva ABC</h1>
            <p className="page-subtitle">
              Classificação de itens por impacto no orçamento
            </p>
          </div>
        </div>

        {/* Aviso de Itens da validação */}
        {location.state?.items && !loading && (
          <div className="bg-blue-50 border border-blue-200 rounded-lg p-4 mb-6">
            <div className="flex gap-3">
              <CheckCircle2 size={20} className="text-blue-600 flex-shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-blue-900">
                  Curva ABC dos serviços-folha
                </h3>
                <p className="text-sm text-blue-700 mt-1">
                  Analisando {items.length}{" "}
                  {items.length === 1 ? "serviço" : "serviços"} (sem grupos/subtotais)
                  {typeof (location.state as { totalOficialOrcamento?: number })
                    ?.totalOficialOrcamento === "number"
                    ? ` · total oficial R$ ${(
                        location.state as { totalOficialOrcamento: number }
                      ).totalOficialOrcamento.toLocaleString("pt-BR", {
                        minimumFractionDigits: 2,
                      })}`
                    : ""}
                  .
                </p>
              </div>
            </div>
          </div>
        )}

        {/* Loading State */}
        {loading && (
          <div className="flex flex-col items-center justify-center py-16">
            <Loader2 className="w-12 h-12 animate-spin text-blue-600 mb-4" />
            <p className="text-slate-600">Carregando dados da Curva ABC...</p>
          </div>
        )}

        {/* Error State */}
        {error && !loading && (
          <div className="bg-red-50 border border-red-200 rounded-lg p-6 mb-8">
            <div className="flex gap-3">
              <AlertCircle size={20} className="text-red-600 flex-shrink-0 mt-0.5" />
              <div>
                <h3 className="font-semibold text-red-900">Erro ao carregar dados</h3>
                <p className="text-sm text-red-700 mt-1">{error}</p>
              </div>
            </div>
          </div>
        )}

        {/* Dados carregados */}
        {!loading && !error && (
          <>
            {bdiAplicado && uploadId && (
              <div className="mb-6 rounded-lg border border-blue-200 bg-blue-50 p-4">
                <p className="text-sm text-blue-900">
                  Este orçamento considera BDI de{" "}
                  <strong>
                    {bdiAplicado.bdiPercentual.toLocaleString("pt-BR", {
                      minimumFractionDigits: 2,
                    })}
                    %
                  </strong>
                </p>
              </div>
            )}
            {items.length === 0 ? (
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-6 mb-8">
                <div className="flex gap-3">
                  <AlertCircle size={20} className="text-amber-600 flex-shrink-0 mt-0.5" />
                  <div>
                    <h3 className="font-semibold text-amber-900">Nenhum item encontrado</h3>
                    <p className="text-sm text-amber-700 mt-1">
                      Não foi possível extrair itens do orçamento. Verifique se o PDF contém tabelas com dados válidos.
                    </p>
                  </div>
                </div>
              </div>
            ) : (
              <>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
          <div className="bg-white rounded-lg p-4 shadow-sm border border-slate-200">
            <p className="text-sm text-slate-600">Total dos itens analisados</p>
            <p className="text-2xl font-bold mt-2">
              R$ {(summary.total / 1000).toFixed(1)}k
            </p>
            <p className="text-xs text-slate-500 mt-1">
              {items.length} {items.length === 1 ? "item" : "itens"}
            </p>
          </div>

          <div className="bg-red-50 rounded-lg p-4 shadow-sm border border-red-200">
            <p className="text-sm text-red-700 font-medium">
              Classe A (Alto impacto)
            </p>
            <p className="text-2xl font-bold text-red-800 mt-2">
              {summary.countA}
            </p>
            <p className="text-xs text-red-600 mt-1">
              {summary.percentA}% do valor
            </p>
          </div>

          <div className="bg-amber-50 rounded-lg p-4 shadow-sm border border-amber-200">
            <p className="text-sm text-amber-700 font-medium">
              Classe B (Médio impacto)
            </p>
            <p className="text-2xl font-bold text-amber-800 mt-2">
              {summary.countB}
            </p>
            <p className="text-xs text-amber-600 mt-1">
              {summary.percentB}% do valor
            </p>
          </div>

          <div className="bg-green-50 rounded-lg p-4 shadow-sm border border-green-200">
            <p className="text-sm text-green-700 font-medium">
              Classe C (Baixo impacto)
            </p>
            <p className="text-2xl font-bold text-green-800 mt-2">
              {summary.countC}
            </p>
            <p className="text-xs text-green-600 mt-1">
              {summary.percentC}% do valor
            </p>
          </div>
        </div>

        {/* Gráfico */}
        <div className="bg-white rounded-lg shadow-md p-6 mb-8">
          <h2 className="text-lg font-semibold mb-6">
            Distribuição por Classe
          </h2>
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={chartData}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis dataKey="name" />
              <YAxis
                yAxisId="left"
                label={{ value: "Itens", angle: -90, position: "insideLeft" }}
              />
              <YAxis
                yAxisId="right"
                orientation="right"
                label={{
                  value: "Valor (R$)",
                  angle: 90,
                  position: "insideRight",
                }}
              />
              <Tooltip
                formatter={(value) => {
                  if (typeof value === "number" && value > 100) {
                    return `R$ ${(value / 1000).toFixed(1)}k`;
                  }
                  return value;
                }}
              />
              <Legend />
              <Bar
                yAxisId="left"
                dataKey="itens"
                fill="#2E7AD4"
                name="Quantidade de itens"
              />
              <Bar
                yAxisId="right"
                dataKey="valor"
                fill="#1F4E78"
                name="Valor total"
              />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Filtros */}
        <div className="bg-white rounded-lg shadow-md p-6 mb-8">
          <h2 className="text-lg font-semibold mb-4">
            Itens por Classificação
          </h2>
          <div className="flex gap-2 mb-6 flex-wrap">
            {[
              { value: "all", label: "Todos" },
              { value: "A", label: "Classe A (Alto impacto)" },
              { value: "B", label: "Classe B (Médio impacto)" },
              { value: "C", label: "Classe C (Baixo impacto)" },
            ].map((filter) => (
              <button
                key={filter.value}
                onClick={() => setSelectedFilter(filter.value as any)}
                className={`px-4 py-2 rounded-lg font-medium transition ${
                  selectedFilter === filter.value
                    ? "bg-blue-600 text-white"
                    : "bg-slate-100 text-slate-700 hover:bg-slate-200"
                }`}
              >
                {filter.label}
              </button>
            ))}
          </div>

          {/* Tabela de Itens */}
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead className="bg-slate-50 border-b">
                <tr>
                  <th className="px-6 py-3 text-left text-sm font-semibold">
                    Classificação
                  </th>
                  <th className="px-6 py-3 text-left text-sm font-semibold">
                    Descrição
                  </th>
                  <th className="px-6 py-3 text-left text-sm font-semibold">
                    Quantidade
                  </th>
                  <th className="px-6 py-3 text-left text-sm font-semibold">
                    Valor Total
                  </th>
                  <th className="px-6 py-3 text-left text-sm font-semibold">
                    % Acumulado
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredItems.map((item) => {
                  const badge = getClassificationBadge(item.classification);
                  return (
                    <tr
                      key={item.id}
                      className="border-b hover:bg-slate-50 transition"
                    >
                      <td className="px-6 py-3">
                        <span
                          className={`inline-flex items-center gap-2 rounded-full px-3 py-1 text-xs font-semibold ${badge.color}`}
                        >
                          <span
                            className={`h-2 w-2 shrink-0 rounded-full ${badge.dot}`}
                            aria-hidden
                          />
                          <span>Classe {item.classification}</span>
                          <span className="sr-only">{badge.label}</span>
                        </span>
                      </td>
                      <td className="px-6 py-3 font-medium text-slate-900">
                        {item.descricao}
                      </td>
                      <td className="px-6 py-3 text-slate-700">
                        {item.quantidade} {item.unidade}
                      </td>
                      <td className="px-6 py-3 font-semibold text-slate-900">
                        R${" "}
                        {Number(item.valor_total || 0).toLocaleString("pt-BR", {
                          minimumFractionDigits: 2,
                        })}
                      </td>
                      <td className="px-6 py-3">
                        <div className="flex items-center gap-2">
                          <div className="w-24 bg-slate-200 rounded-full h-2">
                            <div
                              className="bg-blue-600 h-2 rounded-full"
                              style={{
                                width: `${Number(item.accumulated_percentage || 0)}%`,
                              }}
                            />
                          </div>
                          <span className="text-sm text-slate-600">
                            {Number(item.accumulated_percentage || 0)}%
                          </span>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {/* Info Box */}
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-6 mb-8">
          <div className="flex gap-3">
            <AlertCircle
              size={20}
              className="text-blue-600 flex-shrink-0 mt-0.5"
            />
            <div>
              <h3 className="font-semibold text-blue-900">
                Dica: Curva ABC (Pareto)
              </h3>
              <ul className="text-sm text-blue-800 mt-2 space-y-1">
                <li>
                  • Itens são ordenados pelo <strong>valor total</strong> (maior
                  primeiro). O gráfico de barras usa o <strong>% acumulado</strong>{" "}
                  após cada linha.
                </li>
                <li>
                  • <strong>Classe A:</strong> itens cujo acumulado (após incluir
                  a linha) é ≤ 80% do valor do orçamento.
                </li>
                <li>
                  • <strong>Classe B:</strong> acumulado &gt; 80% e ≤ 95%.{" "}
                  <strong>Classe C:</strong> acumulado &gt; 95%. A quantidade de itens
                  em cada classe depende da concentração do seu orçamento.
                </li>
              </ul>
            </div>
          </div>
        </div>

        {aiError && (
          <div className="bg-red-50 border border-red-200 text-red-700 rounded-lg p-4 mb-6">
            {aiError}
          </div>
        )}

        {aiResult && (
          <div className="bg-emerald-50 border border-emerald-200 text-emerald-800 rounded-lg p-4 mb-6">
            <p className="font-semibold">Análise de IA concluída</p>
            <p className="text-sm mt-1">
              Itens analisados: {aiResult.totalItems || 0} • Valor total: R$ {Number(aiResult.valorTotal || 0).toLocaleString("pt-BR", { minimumFractionDigits: 2 })} • Confiança: {(Number(aiResult.confianca || 0) * 100).toFixed(0)}%
            </p>
          </div>
        )}

        {/* Botões de Ação */}
        <div className="flex gap-4 justify-between">
          <button
            type="button"
            onClick={handleBackToValidacao}
            className="px-6 py-2 bg-slate-200 text-slate-800 rounded-lg hover:bg-slate-300 transition font-medium"
          >
            ← Voltar
          </button>
          <div className="flex gap-3">
            <button
              type="button"
              onClick={() => setExportModalOpen(true)}
              disabled={items.length === 0}
              className="px-6 py-2 rounded-lg transition font-medium flex items-center gap-2 bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-50"
            >
              <Download size={18} />
              Exportar
            </button>
            <button
              onClick={handleAiStandardize}
              disabled={aiLoading}
              className={`px-6 py-2 rounded-lg transition font-medium flex items-center gap-2 ${
                aiLoading
                  ? "bg-blue-400 text-white cursor-not-allowed"
                  : "bg-blue-600 text-white hover:bg-blue-700"
              }`}
            >
              {aiLoading ? "Processando IA..." : "Padronizar com IA"} <ChevronRight size={18} />
            </button>
          </div>
        </div>

        <ExportModal
          open={exportModalOpen}
          onClose={() => setExportModalOpen(false)}
          uploadId={uploadId}
          nomeProjeto={location.state?.nomeProjeto as string | undefined}
          flatItems={flatExportItems}
          defaultModelos={CURVA_ABC_ONLY}
        />
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
};

export default CurvaABC;
