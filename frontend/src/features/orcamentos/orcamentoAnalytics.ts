import type { Orcamento, OrcamentoItem } from "./orcamentoTypes";

export function getItemLineTotal(item: OrcamentoItem): number {
  const explicit =
    typeof (item as { lineTotal?: number }).lineTotal === "number"
      ? (item as { lineTotal: number }).lineTotal
      : undefined;
  if (explicit !== undefined && Number.isFinite(explicit)) return explicit;

  const total =
    typeof item.valor_total === "number"
      ? item.valor_total
      : typeof item.totalValue === "number"
        ? item.totalValue
        : 0;
  if (Number.isFinite(total) && total > 0) return total;

  const qty = Number(item.quantidade ?? item.quantity ?? 0) || 0;
  const unit = Number(item.valor_unitario ?? item.unitValue ?? 0) || 0;
  const bdi = Number((item as { bdi?: number }).bdi ?? 0) || 0;
  return qty * unit * (1 + bdi / 100);
}

export function getOrcamentoTotal(orcamento: Orcamento): number {
  if (!Array.isArray(orcamento.items) || orcamento.items.length === 0) return 0;
  return orcamento.items.reduce((sum, item) => sum + getItemLineTotal(item), 0);
}

export function formatCurrency(value: number): string {
  return value.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

export function formatCurrencyK(
  value: number | string | ReadonlyArray<number | string> | undefined,
): string {
  const raw = Array.isArray(value) ? value[0] : value;
  const num = typeof raw === "number" ? raw : raw ? Number(raw) : 0;
  if (Number.isNaN(num)) return "R$ 0";
  if (Math.abs(num) < 1000) return formatCurrency(num);
  if (Math.abs(num) < 1_000_000) {
    return `R$ ${(num / 1000).toLocaleString("pt-BR", {
      maximumFractionDigits: 1,
    })} mil`;
  }
  return `R$ ${(num / 1_000_000).toLocaleString("pt-BR", {
    maximumFractionDigits: 2,
  })} mi`;
}

export type MonthlyBudgetPoint = {
  month: string;
  value: number;
  planned: number;
};

export function buildMonthlyBudgetSeries(orcamentos: Orcamento[]): MonthlyBudgetPoint[] {
  const completed = orcamentos.filter((o) => o.status === "completed");
  const now = new Date();
  const months: { label: string; start: Date; end: Date }[] = [];

  for (let i = 5; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const start = new Date(d.getFullYear(), d.getMonth(), 1);
    const end = new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59, 999);
    const label = start
      .toLocaleDateString("pt-BR", { month: "short" })
      .replace(".", "");
    months.push({
      label: label.charAt(0).toUpperCase() + label.slice(1),
      start,
      end,
    });
  }

  return months.map((m) => {
    const value = completed
      .filter((o) => o.uploadedAt >= m.start && o.uploadedAt <= m.end)
      .reduce((sum, o) => sum + getOrcamentoTotal(o), 0);
    return { month: m.label, value, planned: value };
  });
}

export type AbcSlice = { name: string; value: number; percentage: number };

export function buildAbcDistribution(orcamentos: Orcamento[]): AbcSlice[] {
  const counts = { A: 0, B: 0, C: 0 };
  let totalValue = 0;

  for (const orc of orcamentos) {
    if (orc.status !== "completed" || !Array.isArray(orc.items)) continue;
    for (const item of orc.items) {
      const cls = String(
        (item as { classification?: string }).classification ?? "",
      ).toUpperCase();
      if (cls !== "A" && cls !== "B" && cls !== "C") continue;
      counts[cls as "A" | "B" | "C"] += getItemLineTotal(item);
    }
  }

  totalValue = counts.A + counts.B + counts.C;
  if (totalValue <= 0) return [];

  return (["A", "B", "C"] as const)
    .map((name) => ({
      name,
      value: counts[name],
      percentage: Math.round((counts[name] / totalValue) * 100),
    }))
    .filter((slice) => slice.value > 0);
}

export type TopQuantityItem = { name: string; value: number };

export function buildTopItemsByQuantity(
  items: OrcamentoItem[],
  limit = 10,
): TopQuantityItem[] {
  const rows = items
    .map((item) => ({
      name: String(item.descricao ?? item.description ?? "—").slice(0, 40),
      value: Number(item.quantidade ?? item.quantity ?? 0) || 0,
    }))
    .filter((r) => r.value > 0)
    .sort((a, b) => b.value - a.value)
    .slice(0, limit);

  return rows;
}

export const CHART_COLORS = ["#1F4E78", "#2E7AD4", "#5B9BD5", "#9FC2E8", "#BFDBF7"];
