import React, { useRef, useState } from "react";
import { toast } from "sonner";
import {
  TrendingUp,
  DollarSign,
  BarChart3,
  PieChart,
  Download,
  Filter,
  RefreshCw,
} from "lucide-react";
import {
  Line,
  AreaChart,
  Area,
  BarChart,
  Bar,
  PieChart as RechartsPieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import jsPDF from "jspdf";
import html2canvas from "html2canvas";
import type { Orcamento } from "../features/orcamentos/orcamentoTypes";
import {
  buildAbcDistribution,
  buildMonthlyBudgetSeries,
  CHART_COLORS,
  formatCurrency,
  formatCurrencyK,
  getOrcamentoTotal,
} from "../features/orcamentos/orcamentoAnalytics";
import { getOrcamentoDisplayName } from "./dashboard/dashboardUtils";
import { btnAccent, btnSecondary } from "./ui/buttonClasses";

interface OrcamentoAnalyticsChartsProps {
  orcamentos: Orcamento[];
  loading: boolean;
  onRefresh?: () => void;
  title?: string;
  subtitle?: string;
  sectionClassName?: string;
}

const ABC_COLORS = ["#1a4f6e", "#2f7aa8", "#0f766e"];

const OrcamentoAnalyticsCharts: React.FC<OrcamentoAnalyticsChartsProps> = ({
  orcamentos,
  loading,
  onRefresh,
  title = "Análise dos orçamentos",
  subtitle = "Dados consolidados dos orçamentos exportados e finalizados",
  sectionClassName = "mt-10",
}) => {
  const [dateRange, setDateRange] = useState("30days");
  const [isExporting, setIsExporting] = useState(false);
  const dashboardRef = useRef<HTMLDivElement>(null);

  const completed = orcamentos.filter((o) => o.status === "completed");
  const totals = {
    totalBudget: completed.reduce((s, o) => s + getOrcamentoTotal(o), 0),
    totalItems: completed.reduce((s, o) => s + (o.itemsFound ?? 0), 0),
  };

  const monthlyData = buildMonthlyBudgetSeries(completed);
  const abcData = buildAbcDistribution(completed);

  const kpis = [
    {
      label: "Orçamento total",
      value: loading ? "—" : formatCurrency(totals.totalBudget),
      icon: <DollarSign className="h-5 w-5" />,
      tone: "bg-thora-steel/10 text-thora-steel",
    },
    {
      label: "Orçamentos analisados",
      value: loading ? "—" : String(completed.length),
      icon: <TrendingUp className="h-5 w-5" />,
      tone: "bg-emerald-50 text-thora-accent",
    },
    {
      label: "Itens (total)",
      value: loading ? "—" : String(totals.totalItems),
      icon: <BarChart3 className="h-5 w-5" />,
      tone: "bg-sky-50 text-thora-sky",
    },
    {
      label: "Média por orçamento",
      value:
        loading || completed.length === 0
          ? "—"
          : formatCurrency(totals.totalBudget / completed.length),
      icon: <PieChart className="h-5 w-5" />,
      tone: "bg-amber-50 text-amber-700",
    },
  ];

  const handleExportDashboard = async () => {
    if (!dashboardRef.current) return;
    try {
      setIsExporting(true);
      const pdf = new jsPDF("p", "mm", "a4");
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 15;

      pdf.setFontSize(20);
      pdf.setFont("helvetica", "bold");
      pdf.text("Dashboard — Thora", margin, margin + 10);
      pdf.setFontSize(10);
      pdf.setFont("helvetica", "normal");
      pdf.text(`Data: ${new Date().toLocaleDateString("pt-BR")}`, margin, margin + 18);

      let yPosition = margin + 35;
      const blocks = dashboardRef.current.querySelectorAll(".chart-card, .kpi-card");

      for (let i = 0; i < blocks.length; i++) {
        const element = blocks[i] as HTMLElement;
        const canvas = await html2canvas(element, {
          scale: 2,
          backgroundColor: "#ffffff",
          logging: false,
        });
        const imgData = canvas.toDataURL("image/png");
        const imgWidth = pageWidth - 2 * margin;
        const imgHeight = (canvas.height * imgWidth) / canvas.width;

        if (yPosition + imgHeight > pageHeight - margin) {
          pdf.addPage();
          yPosition = margin;
        }
        pdf.addImage(imgData, "PNG", margin, yPosition, imgWidth, imgHeight);
        yPosition += imgHeight + 12;
      }

      pdf.save(`Dashboard-Thora-${new Date().toISOString().split("T")[0]}.pdf`);
      toast.success("Dashboard exportado em PDF");
    } catch {
      toast.error("Não foi possível exportar o dashboard");
    } finally {
      setIsExporting(false);
    }
  };

  return (
    <section className={sectionClassName}>
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h2 className="font-display text-xl font-bold text-slate-900 sm:text-2xl">
            {title}
          </h2>
          <p className="mt-1 text-sm text-slate-600">{subtitle}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex items-center gap-2 rounded-xl border border-slate-200/90 bg-white/90 px-3 py-2 text-sm text-slate-600 shadow-sm">
            <Filter className="h-4 w-4 shrink-0" />
            <select
              value={dateRange}
              onChange={(e) => setDateRange(e.target.value)}
              className="bg-transparent text-slate-700 focus:outline-none"
            >
              <option value="7days">Últimos 7 dias</option>
              <option value="30days">Últimos 30 dias</option>
              <option value="90days">Últimos 90 dias</option>
              <option value="all">Todo período</option>
            </select>
          </div>
          {onRefresh && (
            <button
              type="button"
              onClick={onRefresh}
              disabled={loading}
              className={btnSecondary}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              Atualizar
            </button>
          )}
          <button
            type="button"
            onClick={() => void handleExportDashboard()}
            disabled={isExporting || loading || completed.length === 0}
            className={btnAccent}
          >
            <Download className={`h-4 w-4 ${isExporting ? "animate-bounce" : ""}`} />
            {isExporting ? "Exportando…" : "Exportar PDF"}
          </button>
        </div>
      </div>

      <div ref={dashboardRef}>
        <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {kpis.map((kpi) => (
            <div key={kpi.label} className="kpi-card surface-panel p-5">
              <div className={`mb-3 inline-flex rounded-xl p-2.5 ${kpi.tone}`}>
                {kpi.icon}
              </div>
              <p className="text-sm text-slate-500">{kpi.label}</p>
              <p
                className="mt-1 text-xl font-bold tabular-nums tracking-tight text-slate-900 sm:text-2xl"
                title={kpi.value}
              >
                {kpi.value}
              </p>
            </div>
          ))}
        </div>

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
          <div className="chart-card surface-panel p-5 sm:p-6">
            <h3 className="mb-4 font-display text-lg font-semibold text-slate-900">
              Evolução mensal (valor total)
            </h3>
            {completed.length === 0 ? (
              <p className="py-12 text-center text-sm text-slate-500">
                Finalize orçamentos para ver a evolução.
              </p>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <AreaChart data={monthlyData}>
                  <defs>
                    <linearGradient id="colorValue" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#2f7aa8" stopOpacity={0.55} />
                      <stop offset="95%" stopColor="#2f7aa8" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="month" stroke="#94a3b8" tick={{ fontSize: 12 }} />
                  <YAxis
                    stroke="#94a3b8"
                    tick={{ fontSize: 12 }}
                    tickFormatter={(v) => formatCurrencyK(v)}
                    width={72}
                  />
                  <Tooltip
                    formatter={(v) => formatCurrency(Number(v))}
                    contentStyle={{
                      borderRadius: 12,
                      border: "1px solid #e2e8f0",
                    }}
                  />
                  <Legend />
                  <Area
                    type="monotone"
                    dataKey="value"
                    stroke="#1a4f6e"
                    fill="url(#colorValue)"
                    name="Valor exportado"
                  />
                  <Line
                    type="monotone"
                    dataKey="planned"
                    stroke="#d97706"
                    strokeDasharray="5 5"
                    name="Referência"
                    dot={false}
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="chart-card surface-panel p-5 sm:p-6">
            <h3 className="mb-4 font-display text-lg font-semibold text-slate-900">
              Curva ABC (valor por classe)
            </h3>
            {abcData.length === 0 ? (
              <p className="py-12 text-center text-sm text-slate-500">
                Valide orçamentos para gerar classificação ABC nos gráficos.
              </p>
            ) : (
              <div className="flex h-[280px] flex-col gap-4 sm:flex-row sm:items-center">
                <div className="min-h-0 min-w-0 flex-1">
                  <ResponsiveContainer width="100%" height={220}>
                    <RechartsPieChart>
                      <Pie
                        data={abcData}
                        cx="50%"
                        cy="50%"
                        innerRadius={58}
                        outerRadius={88}
                        paddingAngle={2}
                        dataKey="value"
                        nameKey="name"
                        stroke="none"
                      >
                        {abcData.map((_, index) => (
                          <Cell
                            key={`cell-${index}`}
                            fill={ABC_COLORS[index % ABC_COLORS.length]}
                          />
                        ))}
                      </Pie>
                      <Tooltip
                        formatter={(v, _n, item) => {
                          const pct =
                            (item?.payload as { percentage?: number } | undefined)
                              ?.percentage ?? 0;
                          return [`${formatCurrency(Number(v))} (${pct}%)`, "Valor"];
                        }}
                        labelFormatter={(label) => `Classe ${label}`}
                        contentStyle={{
                          borderRadius: 12,
                          border: "1px solid #e2e8f0",
                        }}
                      />
                    </RechartsPieChart>
                  </ResponsiveContainer>
                </div>
                <ul className="flex shrink-0 flex-row flex-wrap justify-center gap-3 sm:w-40 sm:flex-col sm:gap-3">
                  {abcData.map((slice, index) => (
                    <li
                      key={slice.name}
                      className="flex min-w-[7.5rem] items-center gap-2 rounded-xl border border-slate-100 bg-slate-50/80 px-3 py-2"
                    >
                      <span
                        className="h-2.5 w-2.5 shrink-0 rounded-full"
                        style={{
                          backgroundColor: ABC_COLORS[index % ABC_COLORS.length],
                        }}
                      />
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-slate-900">
                          Classe {slice.name}
                        </p>
                        <p className="text-xs tabular-nums text-slate-500">
                          {slice.percentage}% · {formatCurrencyK(slice.value)}
                        </p>
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>

          <div className="chart-card surface-panel p-5 sm:p-6 lg:col-span-2">
            <h3 className="mb-4 font-display text-lg font-semibold text-slate-900">
              Orçamentos por valor total
            </h3>
            {completed.length === 0 ? (
              <p className="py-12 text-center text-sm text-slate-500">
                Nenhum orçamento finalizado ainda.
              </p>
            ) : (
              <ResponsiveContainer width="100%" height={280}>
                <BarChart
                  data={completed.slice(0, 8).map((o) => ({
                    name: getOrcamentoDisplayName(o, 18),
                    value: getOrcamentoTotal(o),
                  }))}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis dataKey="name" stroke="#94a3b8" tick={{ fontSize: 11 }} />
                  <YAxis
                    stroke="#94a3b8"
                    tick={{ fontSize: 12 }}
                    tickFormatter={(v) => formatCurrencyK(v)}
                    width={72}
                  />
                  <Tooltip
                    formatter={(v) => formatCurrency(Number(v))}
                    contentStyle={{
                      borderRadius: 12,
                      border: "1px solid #e2e8f0",
                    }}
                  />
                  <Bar
                    dataKey="value"
                    fill={CHART_COLORS[0]}
                    name="Valor (R$)"
                    radius={[6, 6, 0, 0]}
                  />
                </BarChart>
              </ResponsiveContainer>
            )}
          </div>
        </div>
      </div>
    </section>
  );
};

export default OrcamentoAnalyticsCharts;
