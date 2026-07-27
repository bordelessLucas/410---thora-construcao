import type { Orcamento, OrcamentoItem } from "../../features/orcamentos/orcamentoTypes";
import {
  formatCurrency,
  getOrcamentoTotal,
} from "../../features/orcamentos/orcamentoAnalytics";
import { countByAbcClass, prepareItemsForAiReport } from "../../features/orcamentos/prepareItemsForAiReport";

export { formatCurrency };

export type TendenciaMensalPoint = {
  month: string;
  valor: number;
  quantidade: number;
  monthKey: string;
};

export function getOrcamentoCreatedAt(o: Orcamento): Date {
  const raw = o as Orcamento & { createdAt?: Date; dataUpload?: Date };
  return raw.createdAt ?? raw.dataUpload ?? o.uploadedAt;
}

export function humanizeFileName(raw: string): string {
  const base = raw
    .replace(/^.*[/\\]/, "")
    .replace(/\.[a-z0-9]{2,5}$/i, "")
    .trim();

  if (!base) return raw.trim() || "Orçamento";

  const cleaned = base
    .replace(/^\d+[_-]+/, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();

  if (!cleaned) return "Orçamento";

  return cleaned
    .split(" ")
    .map((word) => {
      if (word.length <= 2 && word === word.toUpperCase()) return word;
      if (/^[A-Z0-9]{2,6}$/.test(word)) return word;
      return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
    })
    .join(" ");
}

export function truncateLabel(text: string, max = 42): string {
  const value = text.trim();
  if (value.length <= max) return value;
  const slice = value.slice(0, Math.max(1, max - 1)).trimEnd();
  return `${slice}…`;
}

export function getOrcamentoDisplayName(o: Orcamento, maxLength?: number): string {
  const preferred = (o.nomeProjeto || "").trim();
  const raw = preferred || o.filename || o.uploadId || "Orçamento";
  const name = preferred ? preferred : humanizeFileName(raw);
  return typeof maxLength === "number" ? truncateLabel(name, maxLength) : name;
}

export function buildTendenciaMensalSeries(orcamentos: Orcamento[]): TendenciaMensalPoint[] {
  const now = new Date();
  const months: { label: string; key: string; start: Date; end: Date }[] = [];

  for (let i = 5; i >= 0; i--) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    const start = new Date(d.getFullYear(), d.getMonth(), 1);
    const end = new Date(d.getFullYear(), d.getMonth() + 1, 0, 23, 59, 59, 999);
    const monthShort = start
      .toLocaleDateString("pt-BR", { month: "short" })
      .replace(".", "");
    const label = `${monthShort.charAt(0).toUpperCase()}${monthShort.slice(1)}/${String(start.getFullYear()).slice(-2)}`;
    const key = `${start.getFullYear()}-${String(start.getMonth() + 1).padStart(2, "0")}`;
    months.push({ label, key, start, end });
  }

  return months.map((m) => {
    const inMonth = orcamentos.filter((o) => {
      const date = getOrcamentoCreatedAt(o);
      return date >= m.start && date <= m.end;
    });
    const processed = inMonth.filter((o) => o.status === "completed");
    return {
      month: m.label,
      monthKey: m.key,
      valor: processed.reduce((sum, o) => sum + getOrcamentoTotal(o), 0),
      quantidade: inMonth.length,
    };
  });
}

export function countMonthsWithTendenciaData(series: TendenciaMensalPoint[]): number {
  return series.filter((p) => p.quantidade > 0 || p.valor > 0).length;
}

export function countClassAItems(orcamento: Orcamento): number {
  if (!Array.isArray(orcamento.items) || orcamento.items.length === 0) return 0;
  const prepared = prepareItemsForAiReport(orcamento.items);
  return countByAbcClass(prepared).A;
}

export function countItemsWithoutPrice(orcamento: Orcamento): number {
  if (!Array.isArray(orcamento.items)) return 0;
  return orcamento.items.filter((item) => isItemWithoutPrice(item)).length;
}

export function isItemWithoutPrice(item: OrcamentoItem): boolean {
  const price =
    (item as { precoUnitario?: number | null }).precoUnitario ??
    item.valor_unitario ??
    item.unitValue ??
    (item as { unitPrice?: number }).unitPrice;

  if (price === null || price === undefined) return true;
  return Number(price) === 0;
}

export function formatRelativeTime(date: Date): string {
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  if (diffMs < 0) return "Agora mesmo";

  const diffMins = Math.floor(diffMs / 60_000);
  const diffHours = Math.floor(diffMs / 3_600_000);
  const diffDays = Math.floor(diffMs / 86_400_000);

  if (diffMins < 1) return "Agora mesmo";
  if (diffMins < 60) return `Há ${diffMins} minuto${diffMins > 1 ? "s" : ""}`;
  if (diffHours < 24) return `Há ${diffHours} hora${diffHours > 1 ? "s" : ""}`;

  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  if (date.toDateString() === yesterday.toDateString()) {
    return `Ontem às ${date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}`;
  }
  if (diffDays < 7) return `${diffDays} dias atrás`;

  return date.toLocaleDateString("pt-BR", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export function isStaleProcessingStatus(o: Orcamento, hours = 48): boolean {
  const status = String(o.status);
  if (status !== "processing" && status !== "queued") return false;
  const created = getOrcamentoCreatedAt(o);
  const ageMs = Date.now() - created.getTime();
  return ageMs > hours * 3_600_000;
}

export type OrcamentoStatusExtended = Orcamento["status"] | "queued";
