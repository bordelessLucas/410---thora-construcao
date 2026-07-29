/**
 * Parser monetário BRL canônico (espelha backend/app/domain/money.py).
 *
 * - 1.234,56 → milhar BR + decimal
 * - 1.234 → milhar BR (3 dígitos após o ponto)
 * - 12.34 → decimal
 */

export function parseBrl(value: unknown): number {
  if (value === null || value === undefined || value === "") return 0;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (typeof value === "number") return Number.isFinite(value) ? value : 0;

  let text = String(value)
    .replace(/R\$/gi, "")
    .replace(/\$/g, "")
    .replace(/%/g, "")
    .replace(/\s/g, "")
    .trim();

  if (!text || text === "." || text === "," || text === "-" || text === "+") {
    return 0;
  }

  let negative = false;
  if (text.startsWith("(") && text.endsWith(")")) {
    negative = true;
    text = text.slice(1, -1).trim();
  }
  if (text.startsWith("-")) {
    negative = true;
    text = text.slice(1).trim();
  }

  if (text.includes(".") && text.includes(",")) {
    if (text.lastIndexOf(",") > text.lastIndexOf(".")) {
      text = text.replace(/\./g, "").replace(",", ".");
    } else {
      text = text.replace(/,/g, "");
    }
  } else if (text.includes(",")) {
    // Só vírgula: sempre decimal BR (inclui coeficientes 0,0006000 / 1,0000000).
    // Nunca remover a vírgula — isso transformava 1,0000000 → 10_000_000.
    const parts = text.split(",");
    if (parts.length === 2 && /^\d+$/.test(parts[1] ?? "")) {
      text = `${parts[0]}.${parts[1]}`;
    } else {
      text = text.replace(/,/g, ".");
    }
  } else if (text.includes(".")) {
    const parts = text.split(".");
    if (parts.length > 2 && parts.every((p) => /^\d+$/.test(p))) {
      text = parts.join("");
    } else if (
      parts.length === 2 &&
      /^\d+$/.test(parts[0] ?? "") &&
      /^\d+$/.test(parts[1] ?? "") &&
      (parts[1]?.length ?? 0) === 3 &&
      (parts[0]?.length ?? 0) <= 3
    ) {
      text = `${parts[0]}${parts[1]}`;
    }
  }

  const parsed = Number.parseFloat(text);
  if (!Number.isFinite(parsed)) return 0;
  return negative ? -parsed : parsed;
}

export function relativeError(expected: number, actual: number): number {
  const denom = Math.max(Math.abs(expected), Math.abs(actual), 1e-9);
  return Math.abs(expected - actual) / denom;
}
