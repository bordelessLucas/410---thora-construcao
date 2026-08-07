/**
 * Recalcula totais com BDI e classificação ABC (Curva de Pareto) para itens de orçamento.
 * Espelha backend/app/domain/abc_curve.py — algoritmo Pareto 80/95 pelo acumulado *antes* do item.
 */

import { parseBrl, relativeError } from "./parseBrl";

export interface OrcamentoItem {
  id: number;
  item?: string;
  tipo?: string;
  banco?: string;
  /** Numeração hierárquica do edital/PDF (ex.: 1.1.2, 2.1.1). */
  code: string;
  /** Código da base de preços (SINAPI, SICRO, cotação, etc.). */
  catalogCode?: string;
  description: string;
  bdi: number;
  unit: string;
  qty: number;
  /** Valor unitário sem BDI (base para o recálculo). */
  unitPrice: number;
  /** Preço total com BDI: qty × unitPrice × (1 + bdi/100) ou VT do edital. */
  lineTotal: number;
  valorUnitarioSemBdi?: number;
  valorUnitarioComBdi?: number;
  valorTotalSemBdi?: number;
  valorTotalComBdi?: number;
  /** Referência do edital antes de ajustes manuais (s/ BDI). */
  referenceUnitPrice?: number;
  /** Total c/ BDI da referência do edital. */
  referenceLineTotal?: number;
  selected?: boolean;
  classification?: "A" | "B" | "C";
  individual_percentage?: number;
  accumulated_percentage?: number;
  /** Confiança da extração híbrida (0–1). */
  extractionConfidence?: number;
  /** Alertas de validação automática (ex.: Qtd×VU≠Total). */
  extractionAlerts?: string[];
  /** Linha com divergência alta — fora da ABC até revisão. */
  quarantine?: boolean;
  abcEligible?: boolean;
}

export function getItemNumero(item: {
  item?: string;
  item_numero?: string;
  code?: string;
}): string {
  const fromItem = String(item.item_numero ?? item.item ?? "").trim();
  const matchItem = fromItem.match(/^(\d+(?:\.\d+)*)/);
  if (matchItem) return matchItem[1];
  const fromCode = String(item.code ?? "").trim();
  // `code` no front às vezes guarda o nº do item (1.2.1); só use se parecer hierarquia
  if (/^\d+(?:\.\d+)+$/.test(fromCode)) return fromCode;
  return fromItem;
}

export function resolveTipoLinha(item: {
  tipo?: string;
  tipo_linha?: string;
  item?: string;
  item_numero?: string;
  quantidade?: number;
  valor_total?: number;
  valorTotalComBdi?: number;
  lineTotal?: number;
  codigo?: string;
  catalogCode?: string;
  code?: string;
  descricao?: string;
  description?: string;
}): "grupo" | "item" | "composicao" {
  const tipo = String(item.tipo_linha ?? item.tipo ?? "").toLowerCase();
  const itemNumero = getItemNumero(item);
  const descricao = String(item.descricao ?? item.description ?? "");
  // catalogCode = código do serviço; `code` no front às vezes é o nº do item — não usar como código
  const codigo = String(item.codigo ?? item.catalogCode ?? "").trim();
  const isXyz = /^\d+\.\d+\.\d+/.test(itemNumero);
  const isGroupNum = /^\d+(?:\.\d+)?$/.test(itemNumero) && !isXyz;
  const total = Number(item.valorTotalComBdi ?? item.lineTotal ?? item.valor_total ?? 0);

  // Tipado como item com valor = folha (inclui serviços ABC sem código SINAPI)
  if (tipo === "item" && total > 0 && !/^GRUPO\b/i.test(descricao.trim())) {
    return "item";
  }

  // Curva ABC / SINAPI: código de catálogo (4+ dígitos) com valor = item executivo
  const catalogCodeLike =
    Boolean(codigo) &&
    (/^\d{4,}(?:-.*)?$/i.test(codigo) || /^[A-Za-z]{1,4}\s*\d{2}/.test(codigo));
  const itemNumeroLooksLikeCatalog = /^\d{4,}$/.test(itemNumero);
  if ((catalogCodeLike || itemNumeroLooksLikeCatalog) && total > 0) {
    return "item";
  }
  if (tipo === "item" && catalogCodeLike) {
    return "item";
  }

  const codigoSeemsCatalog =
    Boolean(codigo) &&
    codigo !== itemNumero &&
    codigo.length <= 40 &&
    !(codigo.includes(" ") && codigo.length > 20);
  // NOVACAP/sintético: código+banco frequentemente vêm na descrição ("106137 SINAPI …")
  const hasCatalogInDescription =
    /\b(SINAPI|SICRO3?|ORSE|TCPO|SEINFRA|SIURB|EMOP|CDHU|DNIT|ANP|Pr[oó]prio|PR[OÓ]PRIA)\b/i.test(
      descricao,
    ) || /^\s*\d{5,}\b/.test(descricao.trim());
  const looksPricedLeaf =
    hasCatalogInDescription && total > 0 && !/^GRUPO\b/i.test(descricao.trim());

  // X ou X.Y sem código de catálogo real = cabeçalho de grupo (sintético)
  // Exceto quando a descrição já carrega código/banco de serviço precificado.
  // Não tratar códigos SINAPI puros (≥4 dígitos) como grupo.
  if (
    isGroupNum &&
    !itemNumeroLooksLikeCatalog &&
    !codigoSeemsCatalog &&
    !looksPricedLeaf
  ) {
    return "grupo";
  }

  // NOVACAP: X.Y.Z com financeiro nunca é composição
  if (isXyz && tipo === "composicao") {
    return "item";
  }
  if (tipo === "grupo" || tipo === "titulo" || tipo === "título" || tipo === "title") {
    if (!codigoSeemsCatalog && !looksPricedLeaf) return "grupo";
  }
  if (tipo === "composicao" || tipo === "composição" || tipo === "insumo" || tipo === "subitem") {
    if (!isXyz) return "composicao";
  }
  // Folha executiva: X.Y.Z ou X.Y com código de catálogo (4.1, 7.1…)
  if (isXyz || (isGroupNum && (codigoSeemsCatalog || looksPricedLeaf))) {
    return "item";
  }
  if (isGroupNum) {
    // Nº sequencial 1..N com valor e tipo=item (Curva ABC) não é cabeçalho de grupo
    if (tipo === "item" && total > 0) return "item";
    return "grupo";
  }
  return tipo === "composicao" ? "composicao" : "item";
}

/** True se nenhum outro item da lista é filho hierárquico (item.). */
export function isHierarchyLeaf(
  itemNumero: string,
  allItemNumeros: Iterable<string>,
): boolean {
  const n = String(itemNumero || "").trim();
  if (!n) return true;
  const prefix = `${n}.`;
  for (const other of allItemNumeros) {
    if (String(other).startsWith(prefix)) return false;
  }
  return true;
}

export function isExecutiveItem(
  item: OrcamentoItem,
  allItemNumeros?: Iterable<string>,
): boolean {
  const tipo = resolveTipoLinha(item);
  const desc = item.description.toLowerCase();
  if (desc.includes("total do grupo")) return false;
  if (item.quarantine === true || item.abcEligible === false) return false;
  const total = Number(item.lineTotal ?? item.valorTotalComBdi ?? 0);
  if (total <= 0) return false;

  const codigo = String(item.catalogCode ?? "").trim();
  const catalogLike =
    /^\d{4,}(?:-.*)?$/i.test(codigo) || /^[A-Za-z]{1,4}\s*\d{2}/.test(codigo);
  // Curva ABC / SINAPI: qualquer serviço precificado com código de catálogo entra
  if (catalogLike) return true;
  if (tipo !== "item") return false;

  // Evita somar grupo + filhos (ex.: 1.2 + 1.2.1), mas mantém serviço
  // precificado mesmo com filho fantasma por numeração inconsistente no PDF.
  const numeros = allItemNumeros ?? [];
  const num = getItemNumero(item);
  if (num && !isHierarchyLeaf(num, numeros)) {
    const qty = Number(item.qty) || 0;
    const vu =
      Number(item.valorUnitarioComBdi) ||
      Number(item.unitPrice) ||
      0;
    const priced =
      qty > 0 && vu > 0 && (Boolean(codigo) || Math.abs(vu - total) > 0.02);
    if (!priced) return false;
  }
  return true;
}

/** @deprecated Prefer parseBrl — mantido como alias. */
export function parseEditableNumber(value: unknown): number {
  return parseBrl(value);
}

export function calcularLineTotalComBdi(
  qty: number,
  unitPrice: number,
  bdi: number,
): number {
  const q = Number(qty) || 0;
  const u = Number(unitPrice) || 0;
  const b = Number(bdi) || 0;
  return q * u * (1 + b / 100);
}

/** Converte preço unitário c/ BDI (extraído do PDF) para base s/ BDI. */
export function unitPriceSemBdiFromComBdi(
  unitComBdi: number,
  bdi: number,
): number {
  const factor = 1 + (Number(bdi) || 0) / 100;
  if (unitComBdi <= 0 || factor <= 0) return unitComBdi;
  return unitComBdi / factor;
}

/** BDI válido para orçamento público brasileiro (0–100%). */
export function sanitizeBdiPercent(value: number): number {
  if (!Number.isFinite(value) || value <= 0) return 0;
  if (value <= 100) return value;
  return 0;
}

/** Infere BDI% a partir de qty, unit s/ BDI e total c/ BDI. */
export function inferBdiPercent(
  qty: number,
  unitPriceSemBdi: number,
  valorTotalComBdi: number,
): number {
  if (qty <= 0 || unitPriceSemBdi <= 0 || valorTotalComBdi <= 0) return 0;
  const base = qty * unitPriceSemBdi;
  if (valorTotalComBdi <= base * 1.001) return 0;
  const inferred = (valorTotalComBdi / base - 1) * 100;
  return inferred > 0 && inferred <= 100 ? Math.round(inferred * 100) / 100 : 0;
}

type StructuredPricingInput = {
  quantidade?: unknown;
  Quantidade?: unknown;
  qty?: unknown;
  valor_unitario?: unknown;
  "Valor Unitário"?: unknown;
  unitPrice?: unknown;
  unitValue?: unknown;
  valor_unitario_sem_bdi?: unknown;
  valor_unitario_com_bdi?: unknown;
  unit_com_bdi?: unknown;
  valor_total?: unknown;
  Total?: unknown;
  totalValue?: unknown;
  valor_total_sem_bdi?: unknown;
  valor_total_com_bdi?: unknown;
  total_com_bdi?: unknown;
  bdi?: unknown;
  BDI?: unknown;
  quarentena?: unknown;
  abc_elegivel?: unknown;
  alertas?: unknown;
  confianca?: unknown;
};

export type ResolvedStructuredPricing = {
  qty: number;
  bdi: number;
  unitPrice: number;
  unitPriceComBdi: number;
  valorTotalSemBdi: number;
  valorTotalComBdi: number;
  quarantine: boolean;
  alerts: string[];
  confidence: number;
};

/**
 * Resolve pricing canônico a partir do item estruturado do backend.
 * Preferência: valor_total_com_bdi do PDF como fonte da ABC.
 */
export function resolveStructuredItemPricing(
  item: StructuredPricingInput,
): ResolvedStructuredPricing {
  const qty = parseBrl(item.quantidade ?? item.Quantidade ?? item.qty);
  const alerts: string[] = Array.isArray(item.alertas)
    ? [...(item.alertas as string[])]
    : [];

  let bdi = sanitizeBdiPercent(
    parseBrl(String(item.bdi ?? item.BDI ?? 0).replace("%", "")),
  );

  let vuSem = parseBrl(item.valor_unitario_sem_bdi);
  let vuCom = parseBrl(item.valor_unitario_com_bdi ?? item.unit_com_bdi);
  let vtSem = parseBrl(item.valor_total_sem_bdi);
  let vtCom = parseBrl(
    item.valor_total_com_bdi ?? item.total_com_bdi ?? item.valor_total ?? item.Total ?? item.totalValue,
  );
  const vuRaw = parseBrl(
    item.valor_unitario ?? item["Valor Unitário"] ?? item.unitValue ?? item.unitPrice,
  );

  const tolerance = 0.02;

  // Correção: total colado na quantidade (extração ABC) → usar Qtd×VU
  if (
    qty > 1 &&
    vuRaw > 0 &&
    vtCom > 0 &&
    Math.abs(vtCom - qty) <= Math.max(0.01, Math.abs(qty) * 1e-9)
  ) {
    const expected = qty * vuRaw;
    if (Math.abs(expected - vtCom) > Math.max(1, expected * 0.02)) {
      vtCom = expected;
      alerts.push("Total igual à quantidade — recalculado como Qtd×VU (custo parcial)");
    }
  }
  if (qty > 0 && vuRaw > 0 && vtCom <= 0) {
    vtCom = qty * vuRaw;
  }

  if (vuSem <= 0 && vuCom <= 0 && vuRaw > 0) {
    if (qty > 0 && vtCom > 0) {
      const errAsCom = relativeError(qty * vuRaw, vtCom);
      const errAsSem =
        bdi > 0
          ? relativeError(qty * vuRaw * (1 + bdi / 100), vtCom)
          : Number.POSITIVE_INFINITY;
      const inferred = bdi <= 0 ? inferBdiPercent(qty, vuRaw, vtCom) : 0;
      const errAsSemInf =
        inferred > 0
          ? relativeError(qty * vuRaw * (1 + inferred / 100), vtCom)
          : Number.POSITIVE_INFINITY;

      if (errAsCom <= tolerance && errAsCom <= errAsSem) {
        vuCom = vuRaw;
        bdi = 0;
        alerts.push("VU interpretado como C/BDI (bate com total)");
      } else if (errAsSem <= tolerance || errAsSemInf <= tolerance) {
        vuSem = vuRaw;
        if (bdi <= 0 && inferred > 0) bdi = inferred;
      } else {
        vuSem = vuRaw;
        alerts.push("VU/total inconsistentes — VT do PDF prevalece");
      }
    } else {
      vuSem = vuRaw;
    }
  }

  if (bdi <= 0 && qty > 0 && vuSem > 0 && vtCom > 0) {
    bdi = inferBdiPercent(qty, vuSem, vtCom);
  }

  const factor = bdi > 0 ? 1 + bdi / 100 : 1;
  if (vuSem <= 0 && vuCom > 0) vuSem = vuCom / factor;
  if (vuCom <= 0 && vuSem > 0) vuCom = vuSem * factor;
  if (vtSem <= 0 && qty > 0 && vuSem > 0) vtSem = qty * vuSem;
  if (vtCom <= 0 && qty > 0 && vuCom > 0) vtCom = qty * vuCom;
  else if (vtCom <= 0 && vtSem > 0) vtCom = vtSem * factor;
  if (vuSem <= 0 && vtSem > 0 && qty > 0) vuSem = vtSem / qty;
  if (vuCom <= 0 && vtCom > 0 && qty > 0) vuCom = vtCom / qty;
  if (vtSem <= 0 && vtCom > 0) vtSem = vtCom / factor;

  let quarantine = item.quarentena === true;
  let confidence =
    typeof item.confianca === "number" && Number.isFinite(item.confianca)
      ? Number(item.confianca)
      : 1;

  if (qty > 0 && vuCom > 0 && vtCom > 0) {
    const err = relativeError(qty * vuCom, vtCom);
    if (err > tolerance) {
      vuCom = vtCom / qty;
      vuSem = vuCom / factor;
      vtSem = qty * vuSem;
      alerts.push(`Qtd×VU≠VT (erro ${(err * 100).toFixed(1)}%) — total do edital usado na ABC`);
      confidence = Math.min(confidence, Math.max(0.45, 1 - Math.min(err, 0.55)));
      // VT do edital prevalece: não coloca em quarentena automaticamente
    }
  } else if (vtCom <= 0 && qty <= 0 && vuSem <= 0) {
    quarantine = true;
    confidence = Math.min(confidence, 0.2);
    alerts.push("Sem quantidade nem preços");
  }
  return {
    qty,
    bdi,
    unitPrice: Math.round(vuSem * 1e6) / 1e6,
    unitPriceComBdi: Math.round(vuCom * 1e6) / 1e6,
    valorTotalSemBdi: Math.round(vtSem * 100) / 100,
    valorTotalComBdi: Math.round(vtCom * 100) / 100,
    quarantine,
    alerts: [...new Set(alerts)],
    confidence,
  };
}

/**
 * Recalcula lineTotal, ordena por valor, percentuais e classificação A/B/C.
 * Classifica pelo acumulado *depois* de incluir o item (A≤80, B≤95, C>95).
 */
export function recalcularCurvaABC(items: OrcamentoItem[]): OrcamentoItem[] {
  const withTotals = items.map((item) => {
    const qty = Number(item.qty) || 0;
    const vu = Number(item.unitPrice) || 0;
    let vtCom = Number(item.valorTotalComBdi) || 0;
    let reference = Number(item.referenceLineTotal) || 0;

    // Total colado na quantidade
    if (
      qty > 1 &&
      vu > 0 &&
      vtCom > 0 &&
      Math.abs(vtCom - qty) <= Math.max(0.01, Math.abs(qty) * 1e-9)
    ) {
      const expected = qty * vu;
      if (Math.abs(expected - vtCom) > Math.max(1, expected * 0.02)) {
        vtCom = expected;
      }
    }
    if (
      qty > 1 &&
      vu > 0 &&
      reference > 0 &&
      Math.abs(reference - qty) <= Math.max(0.01, Math.abs(qty) * 1e-9)
    ) {
      const expected = qty * vu;
      if (Math.abs(expected - reference) > Math.max(1, expected * 0.02)) {
        reference = expected;
      }
    }

    const calculated = calcularLineTotalComBdi(qty, vu, item.bdi);
    const lineTotal =
      reference > 0
        ? reference
        : vtCom > 0
          ? vtCom
          : calculated;
    return {
      ...item,
      qty,
      unitPrice: vu,
      lineTotal,
      valorTotalComBdi: lineTotal,
      referenceLineTotal: reference > 0 ? reference : item.referenceLineTotal,
    };
  });

  const allNumeros = withTotals
    .map((item) => getItemNumero(item))
    .filter(Boolean);

  const groups = withTotals.filter((item) => !isExecutiveItem(item, allNumeros));
  const executives = withTotals.filter((item) => isExecutiveItem(item, allNumeros));

  const sorted = [...executives].sort((a, b) => {
    const diff = b.lineTotal - a.lineTotal;
    if (diff !== 0) return diff;
    return String(a.id).localeCompare(String(b.id), "pt-BR");
  });

  const totalValue = sorted.reduce((acc, item) => acc + item.lineTotal, 0);
  let accumulatedValue = 0;

  const classified = sorted.map((item) => {
    accumulatedValue += item.lineTotal;
    const accumulatedPercentage =
      totalValue > 0 ? (accumulatedValue / totalValue) * 100 : 0;
    const individualPercentage =
      totalValue > 0 ? (item.lineTotal / totalValue) * 100 : 0;

    let classification: "A" | "B" | "C" = "C";
    if (accumulatedPercentage <= 80) {
      classification = "A";
    } else if (accumulatedPercentage <= 95) {
      classification = "B";
    }

    return {
      ...item,
      individual_percentage: individualPercentage,
      accumulated_percentage: accumulatedPercentage,
      classification,
      abcEligible: true,
    };
  });

  const others = groups.map((item) => ({
    ...item,
    classification: undefined,
    individual_percentage: 0,
    accumulated_percentage: 0,
  }));

  return [...classified, ...others];
}

export interface AbcResumo {
  totalGeral: number;
  classeA: { count: number; valor: number };
  classeB: { count: number; valor: number };
  classeC: { count: number; valor: number };
  quarantineCount?: number;
}

export function calcularResumoAbc(items: OrcamentoItem[]): AbcResumo {
  const allNumeros = items.map((item) => getItemNumero(item)).filter(Boolean);
  const executives = items.filter((item) => isExecutiveItem(item, allNumeros));
  const totalGeral = executives.reduce((acc, item) => acc + item.lineTotal, 0);
  const quarantineCount = items.filter((i) => i.quarantine).length;

  const sumByClass = (cls: "A" | "B" | "C") => {
    const filtered = executives.filter((i) => i.classification === cls);
    return {
      count: filtered.length,
      valor: filtered.reduce((acc, i) => acc + i.lineTotal, 0),
    };
  };

  return {
    totalGeral,
    classeA: sumByClass("A"),
    classeB: sumByClass("B"),
    classeC: sumByClass("C"),
    quarantineCount,
  };
}

/** Guarda preço de referência do edital/PDF na primeira vez. */
export function snapshotReferenciaOrcamento(item: OrcamentoItem): OrcamentoItem {
  if (item.referenceLineTotal != null && item.referenceLineTotal > 0) {
    return item;
  }
  const refTotal =
    item.valorTotalComBdi && item.valorTotalComBdi > 0
      ? item.valorTotalComBdi
      : calcularLineTotalComBdi(item.qty, item.unitPrice, item.bdi);
  return {
    ...item,
    referenceUnitPrice: item.unitPrice,
    referenceLineTotal: refTotal,
  };
}

/** Economia quando o preço atual é menor que a referência do edital. */
export function calcularEconomia(item: OrcamentoItem): number {
  const referencia = item.referenceLineTotal ?? 0;
  const atual = item.lineTotal ?? 0;
  if (referencia <= 0 || atual <= 0) return 0;
  return Math.max(0, referencia - atual);
}
