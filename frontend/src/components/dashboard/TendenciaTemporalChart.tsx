import React, { useMemo } from "react";
import { TrendingUp } from "lucide-react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { Orcamento } from "../../features/orcamentos/orcamentoTypes";
import {
  buildTendenciaMensalSeries,
  countMonthsWithTendenciaData,
  formatCurrency,
} from "./dashboardUtils";

interface TendenciaTemporalChartProps {
  orcamentos: Orcamento[];
  loading: boolean;
}

type TooltipPayload = {
  payload?: {
    month: string;
    valor: number;
    quantidade: number;
  };
};

const CustomTooltip: React.FC<{
  active?: boolean;
  payload?: TooltipPayload[];
}> = ({ active, payload }) => {
  if (!active || !payload?.length) return null;
  const data = payload[0]?.payload;
  if (!data) return null;

  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-lg">
      <p className="mb-2 text-sm font-semibold text-slate-900">{data.month}</p>
      <p className="text-sm text-slate-600">
        Valor total:{" "}
        <span className="font-medium text-thora-steel">{formatCurrency(data.valor)}</span>
      </p>
      <p className="text-sm text-slate-600">
        Orçamentos:{" "}
        <span className="font-medium text-thora-accent">{data.quantidade}</span>
      </p>
    </div>
  );
};

const TendenciaTemporalChart: React.FC<TendenciaTemporalChartProps> = ({
  orcamentos,
  loading,
}) => {
  const series = useMemo(() => buildTendenciaMensalSeries(orcamentos), [orcamentos]);
  const monthsWithData = useMemo(() => countMonthsWithTendenciaData(series), [series]);
  const hasEnoughData = monthsWithData >= 2;

  return (
    <div className="surface-panel flex h-full min-h-[320px] flex-col p-5 sm:p-6">
      <div className="mb-4 flex items-center gap-2">
        <div className="rounded-xl bg-thora-steel/10 p-2 text-thora-steel">
          <TrendingUp className="h-5 w-5" />
        </div>
        <div>
          <h2 className="font-display text-lg font-semibold text-slate-900">
            Tendência temporal
          </h2>
          <p className="text-sm text-slate-500">Últimos 6 meses</p>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-1 flex-col gap-4">
          <div className="h-6 w-1/3 animate-pulse rounded bg-slate-200" />
          <div className="flex-1 animate-pulse rounded-xl bg-slate-100" />
        </div>
      ) : !hasEnoughData ? (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 py-12 text-center">
          <TrendingUp className="h-10 w-10 text-slate-300" />
          <p className="text-sm text-slate-500">
            Processe mais orçamentos para ver a tendência
          </p>
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={series} margin={{ top: 8, right: 16, left: 8, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
            <XAxis dataKey="month" stroke="#94a3b8" tick={{ fontSize: 12 }} />
            <YAxis
              yAxisId="left"
              stroke="#1a4f6e"
              tick={{ fontSize: 12 }}
              tickFormatter={(v) =>
                new Intl.NumberFormat("pt-BR", {
                  style: "currency",
                  currency: "BRL",
                  notation: "compact",
                  maximumFractionDigits: 0,
                }).format(v)
              }
            />
            <YAxis
              yAxisId="right"
              orientation="right"
              stroke="#0f766e"
              tick={{ fontSize: 12 }}
              allowDecimals={false}
            />
            <Tooltip content={<CustomTooltip />} />
            <Legend />
            <Line
              yAxisId="left"
              type="monotone"
              dataKey="valor"
              name="Valor total (R$)"
              stroke="#1a4f6e"
              strokeWidth={2}
              dot={{ r: 4, fill: "#1a4f6e" }}
              activeDot={{ r: 6 }}
            />
            <Line
              yAxisId="right"
              type="monotone"
              dataKey="quantidade"
              name="Qtd. processados"
              stroke="#0f766e"
              strokeWidth={2}
              dot={{ r: 4, fill: "#0f766e" }}
              activeDot={{ r: 6 }}
            />
          </LineChart>
        </ResponsiveContainer>
      )}
    </div>
  );
};

export default TendenciaTemporalChart;
