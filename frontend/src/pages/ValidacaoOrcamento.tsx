import React, { useState, useEffect, useMemo, useCallback } from "react";
import { useNavigate, useLocation, useParams } from "react-router-dom";
import { Document, Page, pdfjs } from "react-pdf"; // <--- Imports do PDF
import pdfWorkerSrc from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import {
  ArrowLeft,
  AlertCircle,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Loader2,
  Download,
  Trash2,
  TrendingUp,
} from "lucide-react";
import { toast } from "sonner";
import {
  getOrcamento,
  getOrcamentoFromFirebase,
  getOrcamentoPdf,
} from "../services/api";
import { useAuth } from "../features/auth/AuthContext";
import { upsertOrcamento } from "../features/orcamentos/orcamentoRepository";
import ConfirmDialog from "../components/ConfirmDialog";
import { btnAccent, btnMuted, btnSuccess, iconButton } from "../components/ui/buttonClasses";
import {
  type OrcamentoItem,
  recalcularCurvaABC,
  calcularResumoAbc,
  parseEditableNumber,
  unitPriceSemBdiFromComBdi,
  resolveStructuredItemPricing,
  sanitizeBdiPercent,
  isExecutiveItem,
  getItemNumero,
  resolveTipoLinha,
  snapshotReferenciaOrcamento,
  calcularEconomia,
  detectarDuplaContagemAbc,
} from "../features/orcamentos/recalcularCurvaABC";
import type { NovoOrcamentoFlowState } from "../features/orcamentos/outputModels";
import { CURVA_ABC_ONLY } from "../features/orcamentos/outputModels";
import { exportOrcamentoExcel } from "../features/orcamentos/exportOrcamento";
import { mapRawListToLinhasAnaliticas } from "../features/orcamentos/orcamentoAnalitico";
import { recalcularGruposAnalitico } from "../features/orcamentos/recalcularAnaliticoHierarquico";
import { useOrcamentoLinhasContext } from "../features/orcamentos/OrcamentoLinhasContext";
import { WizardStepper } from "../components/WizardStepper";
import {
  ANALISE_ORCAMENTO_VALIDATION_STEP,
  ANALISE_ORCAMENTO_WIZARD_STEPS,
} from "../features/orcamentos/novoOrcamentoWizard";
import {
  analisarOrcamentoFromItens,
  mensagensAnaliseLinha,
  resultadoAnalisePorId,
} from "../features/orcamentos/analiseOrcamento";
import type { DadosExtraidosLinha } from "../features/orcamentos/analiseOrcamento/correcaoAnalise";
import { AnaliseOrcamentoResumo } from "../components/orcamento/AnaliseOrcamentoResumo";
import { AnaliseOrcamentoStatusBadge } from "../components/orcamento/AnaliseOrcamentoStatusBadge";

// --- CONFIGURAÇÃO OBRIGATÓRIA DO WORKER (PARA VITE) ---
// `?url` faz o Vite emitir o arquivo estático com URL correta (evita CORS do CDN e 404 por path relativo a esta página).
pdfjs.GlobalWorkerOptions.workerSrc = pdfWorkerSrc;

type ItemOrcamento = OrcamentoItem;

const EDITABLE_NUMERIC_CLASS =
  "box-border w-full rounded-md border border-slate-200/90 bg-slate-50 px-2 py-1.5 text-right text-sm font-medium tabular-nums transition hover:border-slate-300 hover:bg-slate-100 focus:border-blue-400 focus:bg-white focus:outline-none focus:ring-1 focus:ring-blue-300 [appearance:textfield] [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none";

interface TableRow {
  [key: string]: string | number;
}

interface ExtractedTable {
  page: number;
  table_id: string;
  rows: TableRow[][];
}

/** Recortes das tabelas escolhidas na tela anterior (mesmo payload do detect-tables). */
interface SelectedTablePreview {
  id: string;
  name: string;
  page: number;
  imagem_base64?: string;
}

interface StructuredBudgetItem {
  item?: string | number;
  tipo?: string;
  banco?: string;
  codigo?: string;
  Código?: string;
  bdi?: number | string;
  BDI?: number | string;
  descricao?: string;
  Descrição?: string;
  unidade?: string;
  Unidade?: string;
  quantidade?: number | string;
  Quantidade?: number | string;
  valor_unitario?: number | string;
  "Valor Unitário"?: number | string;
  valor_total?: number | string;
  Total?: number | string;
  confianca?: number;
  alertas?: string[];
}

const toNumber = (value: unknown): number => {
  if (typeof value === "number") return value;
  if (typeof value !== "string") return 0;
  const compact = value.replace("R$", "").replace(/\s/g, "");
  const normalized =
    compact.includes(",") && compact.includes(".")
      ? compact.replace(/\./g, "").replace(",", ".")
      : compact.includes(",")
        ? compact.replace(",", ".")
        : compact;
  const parsed = Number.parseFloat(normalized);
  return Number.isFinite(parsed) ? parsed : 0;
};

const toBdiPercent = (value: unknown): number => {
  if (typeof value === "number") return sanitizeBdiPercent(value);
  if (typeof value !== "string") return 0;
  const compact = value.replace("%", "").replace(/\s/g, "");
  return sanitizeBdiPercent(toNumber(compact));
};

const mapStructuredItemsToValidation = (
  items: StructuredBudgetItem[],
): ItemOrcamento[] => {
  const mapped: ItemOrcamento[] = [];
  let id = 0;

  for (const item of items) {
    const description = String(item.descricao ?? item.Descrição ?? "").trim();
    const itemNumero = String(item.item ?? item.item_numero ?? "").trim();
    const codigoCatalogo = String(item.codigo ?? item.Código ?? "").trim();
    const tipoHint = String(item.tipo_linha ?? item.tipo ?? "").toLowerCase();
    const tipo = resolveTipoLinha({
      tipo: tipoHint,
      tipo_linha: tipoHint,
      item: itemNumero,
      item_numero: itemNumero,
      codigo: codigoCatalogo,
      catalogCode: codigoCatalogo,
      descricao: description,
      description,
      valor_total: Number(item.valor_total_com_bdi ?? item.valor_total ?? 0),
      valorTotalComBdi: Number(item.valor_total_com_bdi ?? item.valor_total ?? 0),
    });
    const pricing = resolveStructuredItemPricing(item);
    const {
      qty,
      bdi,
      unitPrice,
      unitPriceComBdi,
      valorTotalSemBdi,
      valorTotalComBdi,
      quarantine,
      alerts,
      confidence,
    } = pricing;

    // Serviço precificado nunca é descartado como "grupo" (nº sequencial sem SINAPI)
    let tipoFinal: typeof tipo = tipo;
    // NÃO promover grupo→item só porque tem valor (causa dupla contagem na ABC:
    // 10 + 10.1 + 10.2...). Grupos permanecem na planilha; a ABC usa só folhas.
    if (description.toLowerCase().includes("total do grupo")) {
      continue;
    }
    // Mantém grupos na validação (não edita como serviço)
    if (tipoFinal === "grupo") {
      // ainda inclui na lista para hierarquia/visualização
    }

    const backendClassification = (item as { classification?: string }).classification;
    const classification =
      backendClassification === "A" ||
      backendClassification === "B" ||
      backendClassification === "C"
        ? backendClassification
        : undefined;

    mapped.push({
      id: ++id,
      item: itemNumero,
      tipo: tipoFinal,
      banco: String(item.banco ?? ""),
      code: itemNumero || codigoCatalogo || String(id).padStart(3, "0"),
      catalogCode: codigoCatalogo || undefined,
      description,
      bdi,
      unit: String(item.unidade ?? item.Unidade ?? "un").trim() || "un",
      qty,
      unitPrice,
      lineTotal: valorTotalComBdi > 0 ? valorTotalComBdi : 0,
      valorUnitarioSemBdi: unitPrice,
      valorUnitarioComBdi: unitPriceComBdi,
      valorTotalSemBdi,
      valorTotalComBdi,
      referenceUnitPrice: unitPrice > 0 ? unitPrice : undefined,
      referenceLineTotal: valorTotalComBdi > 0 ? valorTotalComBdi : undefined,
      selected: false,
      classification,
      quarantine,
      abcEligible: quarantine ? false : (item as { abc_elegivel?: boolean }).abc_elegivel !== false,
      extractionConfidence: confidence,
      extractionAlerts: alerts.length
        ? alerts
        : Array.isArray(item.alertas)
          ? item.alertas
          : undefined,
    });
  }

  return mapped;
};

const mapStoredItemsToValidation = (rawItems: unknown[]): ItemOrcamento[] => {
  const mapped: ItemOrcamento[] = [];
  let id = 0;

  for (const raw of rawItems) {
    if (!raw || typeof raw !== "object") continue;
    const item = raw as Record<string, unknown>;
    const tipo = String(item.tipo ?? "item").toLowerCase();
    const description = String(item.descricao ?? item.description ?? "").trim();
    if (description.toLowerCase().includes("total do grupo")) {
      continue;
    }
    // Mantém grupos na planilha; ABC filtra folhas via isExecutiveItem

    const qty = toNumber(item.quantidade ?? item.quantity ?? item.qty);
    const bdi = toBdiPercent(item.bdi);
    const pricing = resolveStructuredItemPricing({
      quantidade: qty,
      valor_unitario: item.valor_unitario ?? item.unitValue,
      unitPrice: item.unitPrice,
      valor_total: item.valor_total ?? item.totalValue,
      totalValue: item.totalValue,
      bdi,
    });
    const unitFromStore =
      item.unitPrice !== undefined && Number(item.unitPrice) > 0
        ? toNumber(item.unitPrice)
        : pricing.unitPrice;
    const storedLineTotal = toNumber(
      item.lineTotal ?? item.valor_total ?? item.totalValue,
    );

    mapped.push({
      id: ++id,
      item: String(item.item ?? item.item_numero ?? ""),
      tipo: String(item.tipo ?? "item"),
      banco: String(item.banco ?? ""),
      code:
        String(item.item ?? item.item_numero ?? item.codigo ?? item.code ?? id).trim() ||
        String(id).padStart(3, "0"),
      catalogCode:
        typeof item.catalogCode === "string"
          ? item.catalogCode
          : String(item.codigo ?? "").trim() || undefined,
      description,
      bdi,
      unit: String(item.unidade ?? item.unit ?? "un").trim() || "un",
      qty: pricing.qty || qty,
      unitPrice: unitFromStore,
      lineTotal: storedLineTotal > 0 ? storedLineTotal : 0,
      referenceUnitPrice:
        typeof item.referenceUnitPrice === "number"
          ? item.referenceUnitPrice
          : unitFromStore > 0
            ? unitFromStore
            : undefined,
      referenceLineTotal:
        typeof item.referenceLineTotal === "number"
          ? item.referenceLineTotal
          : storedLineTotal > 0
            ? storedLineTotal
            : undefined,
      selected: false,
      classification: item.classification as "A" | "B" | "C" | undefined,
      individual_percentage:
        typeof item.individual_percentage === "number"
          ? item.individual_percentage
          : undefined,
      accumulated_percentage:
        typeof item.accumulated_percentage === "number"
          ? item.accumulated_percentage
          : undefined,
      extractionConfidence:
        typeof item.confianca === "number" ? item.confianca : undefined,
      extractionAlerts: Array.isArray(item.alertas)
        ? (item.alertas as string[])
        : undefined,
    });
  }

  return mapped;
};

function buildItemExportPayload(items: ItemOrcamento[]): Record<string, unknown>[] {
  return items.map((item) => ({
    id: String(item.id),
    item: item.item ?? "",
    item_numero: item.item ?? "",
    tipo: item.tipo ?? "item",
    banco: item.banco ?? "",
    codigo: item.catalogCode ?? "",
    catalogCode: item.catalogCode ?? "",
    code: item.item ?? item.code,
    descricao: item.description,
    description: item.description,
    unidade: item.unit,
    unit: item.unit,
    quantidade: item.qty,
    qty: item.qty,
    valor_unitario: item.valorUnitarioSemBdi ?? item.unitPrice,
    valor_unitario_sem_bdi: item.valorUnitarioSemBdi ?? item.unitPrice,
    valor_unitario_com_bdi: item.valorUnitarioComBdi ?? undefined,
    unitPrice: item.valorUnitarioSemBdi ?? item.unitPrice,
    unitPriceComBdi: item.valorUnitarioComBdi ?? undefined,
    valor_total: item.lineTotal,
    valor_total_com_bdi: item.valorTotalComBdi ?? item.lineTotal,
    valor_total_sem_bdi: item.valorTotalSemBdi ?? undefined,
    lineTotal: item.lineTotal,
    totalValue: item.lineTotal,
    bdi: item.bdi,
    classification: item.classification,
    individual_percentage: item.individual_percentage,
    accumulated_percentage: item.accumulated_percentage,
  }));
}

export default function ValidacaoOrcamento() {
  const navigate = useNavigate();
  const location = useLocation();
  const flowState = location.state as NovoOrcamentoFlowState | null;
  const { uploadId: uploadIdFromRoute } = useParams<{ uploadId: string }>();
  const { user } = useAuth();
  const { setOrcamentoLinhas } = useOrcamentoLinhasContext();

  const isReopenedAnalysis = !flowState?.file && Boolean(uploadIdFromRoute);

  const resolvedUploadId =
    (flowState?.uploadId as string | undefined) ?? uploadIdFromRoute;

  // States da Planilha
  const [items, setItems] = useState<ItemOrcamento[]>([]);
  const [hierarchicalItems, setHierarchicalItems] = useState<unknown[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string>("");
  const [isExporting, setIsExporting] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [deleteItemId, setDeleteItemId] = useState<number | null>(null);
  const [selectAll, setSelectAll] = useState(false);

  const abcResumo = useMemo(() => calcularResumoAbc(items), [items]);
  const executiveItemNumeros = useMemo(
    () => items.map((item) => getItemNumero(item)).filter(Boolean),
    [items],
  );
  const isExecutivo = useCallback(
    (item: ItemOrcamento) => isExecutiveItem(item, executiveItemNumeros),
    [executiveItemNumeros],
  );

  const rawItemsParaAnalise = useMemo(() => {
    if (hierarchicalItems.length > 0) return hierarchicalItems;
    return items.map((item) => ({
      id: item.id,
      item: item.item,
      item_numero: item.item,
      tipo: item.tipo,
      banco: item.banco,
      codigo: item.catalogCode ?? "",
      descricao: item.description,
      unidade: item.unit,
      quantidade: item.qty,
      bdi: item.bdi,
      valor_unitario: item.unitPrice,
      unitPrice: item.unitPrice,
      valor_total: item.lineTotal,
      lineTotal: item.lineTotal,
    }));
  }, [hierarchicalItems, items]);

  const resultadoAnalise = useMemo(
    () => analisarOrcamentoFromItens(items, rawItemsParaAnalise),
    [items, rawItemsParaAnalise],
  );

  const analisePorId = useMemo(
    () => resultadoAnalisePorId(resultadoAnalise),
    [resultadoAnalise],
  );

  const handleVerLinhaAnalise = useCallback((linhaId: string | number) => {
    const el = document.getElementById(`orcamento-linha-${linhaId}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.classList.add("ring-2", "ring-blue-500", "ring-offset-1");
    window.setTimeout(() => {
      el.classList.remove("ring-2", "ring-blue-500", "ring-offset-1");
    }, 2500);
  }, []);

  const dadosExtraidosPorId = useMemo(() => {
    const map = new Map<string | number, DadosExtraidosLinha>();
    for (const item of items) {
      map.set(item.id, {
        itemNumero: item.item,
        codigo: item.catalogCode ?? item.code,
        banco: item.banco,
        descricao: item.description,
        unidade: item.unit,
        quantidade: item.qty,
        precoUnitario: item.unitPrice,
        precoTotalComBdi: item.lineTotal,
        bdiPercent: item.bdi,
      });
    }
    return map;
  }, [items]);

  const economiaTotal = useMemo(
    () => items.reduce((sum, item) => sum + calcularEconomia(item), 0),
    [items],
  );

  const editalSemPrecos = useMemo(() => {
    const executive = items.filter(isExecutivo);
    if (executive.length === 0) return false;
    return executive.every((item) => item.unitPrice <= 0 && item.lineTotal <= 0);
  }, [items, isExecutivo]);

  const applyAbcToItems = (raw: ItemOrcamento[]) =>
    recalcularCurvaABC(raw.map(snapshotReferenciaOrcamento));

  // States do PDF Viewer
  const [pdfFile, setPdfFile] = useState<File | null>(null);
  const [numPages, setNumPages] = useState<number>(0);
  const [pageNumber, setPageNumber] = useState<number>(1);
  const [scale, setScale] = useState<number>(1.0); // Zoom

  const nomeProjetoExport = useMemo(
    () =>
      (flowState?.nomeProjeto as string | undefined) ||
      (flowState?.filename as string | undefined)?.replace(/\.pdf$/i, "") ||
      pdfFile?.name?.replace(/\.pdf$/i, "") ||
      undefined,
    [flowState?.nomeProjeto, flowState?.filename, pdfFile?.name],
  );

  const validacaoFinanceiraAlertas = useMemo(() => {
    const resumo = flowState?.structuredData?.resumo as
      | { validacao_financeira?: { alertas?: unknown; ok?: boolean } }
      | undefined;
    const vf = resumo?.validacao_financeira;
    if (!vf) return [] as string[];
    const alertas = Array.isArray(vf.alertas)
      ? vf.alertas.filter((a): a is string => typeof a === "string" && a.trim().length > 0)
      : [];
    return alertas;
  }, [flowState?.structuredData?.resumo]);

  const totalOficialOrcamento = useMemo(() => {
    const resumo = flowState?.structuredData?.resumo as
      | {
          valor_total?: number;
          validacao_financeira?: {
            total_geral?: {
              total_geral_documento?: number;
              soma_folhas?: number;
            };
          };
        }
      | undefined;
    const doc = Number(
      resumo?.validacao_financeira?.total_geral?.total_geral_documento || 0,
    );
    const valor = Number(resumo?.valor_total || 0);
    return doc > 0 ? doc : valor > 0 ? valor : 0;
  }, [flowState?.structuredData?.resumo]);

  const duplaContagemAbc = useMemo(() => {
    return detectarDuplaContagemAbc(abcResumo.totalGeral, totalOficialOrcamento);
  }, [abcResumo.totalGeral, totalOficialOrcamento]);

  const estimativoMeta = useMemo(() => {
    const resumo = flowState?.structuredData?.resumo as
      | { estimativo_meta?: Record<string, unknown>; document_kind?: string }
      | undefined;
    const meta =
      (resumo?.estimativo_meta as Record<string, unknown> | undefined) ||
      ((flowState?.structuredData as { estimativo_meta?: Record<string, unknown> } | undefined)
        ?.estimativo_meta);
    if (!meta || typeof meta !== "object") return null;
    if (resumo?.document_kind !== "estimativo_srp" && meta.document_kind !== "estimativo_srp") {
      return null;
    }
    return meta;
  }, [flowState?.structuredData]);

  useEffect(() => {
    if (!resolvedUploadId || items.length === 0) return;
    const raw =
      hierarchicalItems.length > 0 ? hierarchicalItems : buildItemExportPayload(items);
    const linhas = recalcularGruposAnalitico(mapRawListToLinhasAnaliticas(raw));
    if (linhas.length === 0) return;
    setOrcamentoLinhas({
      linhas,
      uploadId: resolvedUploadId,
      nomeProjeto: nomeProjetoExport ?? "Orçamento",
    });
  }, [items, hierarchicalItems, resolvedUploadId, nomeProjetoExport, setOrcamentoLinhas]);

  const selectedTablePreviews = useMemo(() => {
    const raw = flowState?.selectedTablePreviews as SelectedTablePreview[] | undefined;
    if (!Array.isArray(raw)) return [];
    return raw.filter(
      (p) =>
        p &&
        typeof p.imagem_base64 === "string" &&
        p.imagem_base64.trim().length > 0,
    );
  }, [flowState]);

  const showSelectedTableImages = selectedTablePreviews.length > 0;

  useEffect(() => {
    if (!uploadIdFromRoute && !flowState?.structuredData && !flowState?.extractedData) {
      navigate("/analise-orcamento", { replace: true });
      return;
    }

    const load = async () => {
      setIsLoading(true);
      setLoadError("");

      if (flowState?.file) {
        setPdfFile(flowState.file);
      }

      const structuredItems = flowState?.structuredData?.items as
        | StructuredBudgetItem[]
        | undefined;

      // Retorno da Curva ABC: state pode trazer items/editedItems no topo
      const stateItemsFallback = (
        (flowState as { items?: StructuredBudgetItem[]; editedItems?: StructuredBudgetItem[] } | null)
          ?.items ??
        (flowState as { editedItems?: StructuredBudgetItem[] } | null)?.editedItems
      );

      const itemsFromFlow =
        Array.isArray(structuredItems) && structuredItems.length > 0
          ? structuredItems
          : Array.isArray(stateItemsFallback) && stateItemsFallback.length > 0
            ? stateItemsFallback
            : null;

      if (itemsFromFlow) {
        const hierarchical =
          (flowState?.hierarchicalItems as unknown[] | undefined) ??
          (flowState?.structuredData?.hierarchicalItems as unknown[] | undefined) ??
          itemsFromFlow;
        setHierarchicalItems(hierarchical);
        setItems(applyAbcToItems(mapStructuredItemsToValidation(itemsFromFlow)));

        // Se voltou da Curva ABC sem File em memória, tenta recuperar o PDF
        if (!flowState?.file && resolvedUploadId) {
          try {
            const pdfBlob = await getOrcamentoPdf(resolvedUploadId);
            if (pdfBlob) {
              setPdfFile(
                new File(
                  [pdfBlob],
                  (flowState?.filename as string | undefined) ?? `${resolvedUploadId}.pdf`,
                  { type: "application/pdf" },
                ),
              );
            }
          } catch {
            // PDF opcional na validação em memória
          }
        }

        setIsLoading(false);
        return;
      }

      const extractedTables = flowState?.extractedData as ExtractedTable[] | undefined;
      if (Array.isArray(extractedTables) && extractedTables.length > 0) {
        try {
          const parsedItems = parseExtractedTables(extractedTables);
          setItems(applyAbcToItems(parsedItems));
        } catch {
          setLoadError("Erro ao processar dados extraídos do PDF");
        }
        setIsLoading(false);
        return;
      }

      const uploadId = resolvedUploadId;
      if (!uploadId) {
        setLoadError("Orçamento não identificado.");
        setIsLoading(false);
        return;
      }

      try {
        const [firebaseDoc, backendDoc, pdfBlob] = await Promise.all([
          getOrcamentoFromFirebase(uploadId).catch(() => null),
          getOrcamento(uploadId).catch(() => null),
          getOrcamentoPdf(uploadId).catch(() => null),
        ]);

        const itemsData =
          (firebaseDoc?.itemsData as Record<string, unknown> | undefined) ??
          (backendDoc?.orcamento?.itemsData as Record<string, unknown> | undefined);

        const rawHierarchical =
          (itemsData?.hierarchical_items as unknown[]) ??
          (backendDoc?.orcamento?.hierarchical_items as unknown[]) ??
          [];

        const rawItems =
          (firebaseDoc?.items as unknown[]) ??
          (backendDoc?.orcamento?.items as unknown[]) ??
          (backendDoc?.items as unknown[]) ??
          (itemsData?.items as unknown[]) ??
          [];

        if (!Array.isArray(rawItems) || rawItems.length === 0) {
          setLoadError(
            "Nenhum item salvo para este orçamento. Processe o PDF em Novo Orçamento.",
          );
          setIsLoading(false);
          return;
        }

        setHierarchicalItems(
          Array.isArray(rawHierarchical) && rawHierarchical.length > 0
            ? rawHierarchical
            : rawItems,
        );
        setItems(applyAbcToItems(mapStoredItemsToValidation(rawItems)));

        if (pdfBlob) {
          setPdfFile(
            new File([pdfBlob], firebaseDoc?.filename ?? `${uploadId}.pdf`, {
              type: "application/pdf",
            }),
          );
        }
      } catch (e: unknown) {
        const msg = e instanceof Error ? e.message : "Erro ao carregar orçamento";
        setLoadError(msg);
      } finally {
        setIsLoading(false);
      }
    };

    void load();
  }, [flowState, uploadIdFromRoute, resolvedUploadId, navigate]);

  // Função para normalizar números (converte vírgula em ponto)
  const parseNumber = (value: any): number => {
    if (value === null || value === undefined || value === "") return 0;

    let numStr = String(value).trim();

    // Se contém vírgula como separador decimal (formato português)
    if (numStr.includes(",")) {
      // Remove espaços e pontos (usados como mil separator)
      numStr = numStr.replace(/\s/g, "").replace(/\./g, "");
      // Converte vírgula em ponto
      numStr = numStr.replace(",", ".");
    }

    const result = parseFloat(numStr);
    console.log(`📊 Convertendo: "${value}" → ${result}`);
    return isNaN(result) ? 0 : result;
  };

  // Função para detectar se uma linha é cabeçalho
  const isHeaderRow = (row: TableRow[]): boolean => {
    const headerKeywords = [
      "descrição",
      "description",
      "unidade",
      "unit",
      "quantidade",
      "quantity",
      "valor",
      "preço",
      "price",
      "unitário",
      "tipo",
      "type",
      "quant",
      "un.",
      "und",
      "item",
      "código",
      "code",
      "banco",
      "bank",
    ];

    // Verifica se a maioria das células contém palavras-chave de cabeçalho
    const headerCells = row.filter((cell) => {
      const cellText = String(cell || "")
        .toLowerCase()
        .trim();
      return (
        headerKeywords.some((keyword) => cellText.includes(keyword)) ||
        cellText === "" // Células vazias no cabeçalho também contam
      );
    });

    return headerCells.length >= row.length * 0.6; // Se 60%+ parecem ser cabeçalho
  };

  // Função para parsear as tabelas extraídas
  const parseExtractedTables = (tables: ExtractedTable[]): ItemOrcamento[] => {
    const items: ItemOrcamento[] = [];
    let id = 0;

    tables.forEach((table) => {
      let descCol = -1,
        bdiCol = -1,
        unCol = -1,
        qtdCol = -1,
        vuCol = -1;

      table.rows.forEach((row, idx) => {
        // PRIMEIRA LINHA OU CABEÇALHO: Identificar índices das colunas
        if (idx === 0 || isHeaderRow(row)) {
          row.forEach((cell, colIdx) => {
            const cellText = String(cell || "")
              .toLowerCase()
              .trim();

            // Procura por palavras-chave em cada coluna
            if (
              cellText.includes("descrição") ||
              cellText.includes("description") ||
              cellText.includes("descr")
            ) {
              descCol = colIdx;
              if (idx === 0)
                console.log(`✓ Coluna Descrição encontrada: índice ${colIdx}`);
            } else if (
              cellText === "bdi" ||
              cellText.includes("bdi") ||
              cellText.includes("b.d.i")
            ) {
              bdiCol = colIdx;
              if (idx === 0)
                console.log(`✓ Coluna BDI encontrada: índice ${colIdx}`);
            } else if (
              cellText.includes("unidade") ||
              cellText.includes("un.") ||
              cellText.includes("un") ||
              cellText.includes("unit")
            ) {
              unCol = colIdx;
              if (idx === 0)
                console.log(`✓ Coluna Unidade encontrada: índice ${colIdx}`);
            } else if (
              cellText.includes("qtd") ||
              cellText.includes("quant") ||
              cellText.includes("quantity") ||
              cellText.includes("quantidade")
            ) {
              qtdCol = colIdx;
              if (idx === 0)
                console.log(`✓ Coluna Quantidade encontrada: índice ${colIdx}`);
            } else if (
              cellText.includes("valor") ||
              cellText.includes("preço") ||
              cellText.includes("price") ||
              cellText.includes("unitário") ||
              cellText.includes("unit.")
            ) {
              vuCol = colIdx;
              if (idx === 0)
                console.log(
                  `✓ Coluna Valor Unit. encontrada: índice ${colIdx}`,
                );
            }
          });
          return; // Pula o cabeçalho
        }

        // PRÓXIMAS LINHAS: Extrair dados usando os índices identificados
        if (row && row.length >= 1) {
          // Se os índices foram encontrados, usa-os; senão tenta ordem padrão
          const colDesc = descCol >= 0 ? descCol : 3;
          const colBdi = bdiCol >= 0 ? bdiCol : -1;
          const colUn = unCol >= 0 ? unCol : 4;
          const colQtd = qtdCol >= 0 ? qtdCol : 5;
          const colVu = vuCol >= 0 ? vuCol : 6;

          const description = String(row[colDesc] || "").trim();
          const bdi = colBdi >= 0 ? toBdiPercent(row[colBdi]) : 0;
          const unit = String(row[colUn] || "un").trim();
          const qty = parseNumber(row[colQtd]);
          const unitComBdi = parseNumber(row[colVu]);
          const unitPrice = unitPriceSemBdiFromComBdi(unitComBdi, bdi);

          // Validação: descrição não pode ser numérica simples (como "1.1", "2.1")
          const isNumeric =
            /^\d+(\.\d+)?$/.test(description) || description === "";
          if (description && !isNumeric) {
            items.push({
              id: id++,
              code: `${id.toString().padStart(3, "0")}`,
              description,
              bdi,
              unit,
              qty,
              unitPrice,
              lineTotal: 0,
            });
          }
        }
      });
    });

    return items;
  };

  const handleCellEdit = (
    id: number,
    field: "qty" | "unitPrice" | "bdi",
    rawValue: string,
  ) => {
    const numericValue = parseEditableNumber(rawValue);
    setItems((prev) => {
      const updated = prev.map((item) =>
        item.id === id ? { ...item, [field]: numericValue } : item,
      );
      return recalcularCurvaABC(updated);
    });
  };

  const handleChange = (
    id: number,
    field: keyof ItemOrcamento,
    value: string | number,
  ) => {
    setItems((prev) => {
      const updated = prev.map((item) =>
        item.id === id ? { ...item, [field]: value } : item,
      );
      if (field === "tipo") {
        return recalcularCurvaABC(updated);
      }
      return updated;
    });
  };

  const confirmRemoveItem = () => {
    if (deleteItemId == null) return;
    setItems((prev) => recalcularCurvaABC(prev.filter((item) => item.id !== deleteItemId)));
    setDeleteItemId(null);
    toast.success("Item removido");
  };

  const handleAddItem = () => {
    const newItem: ItemOrcamento = {
      id: (items.length ? Math.max(...items.map((i) => i.id)) : 0) + 1,
      code: `${(items.length + 1).toString().padStart(3, "0")}`,
      description: "",
      bdi: 0,
      unit: "un",
      qty: 0,
      unitPrice: 0,
      lineTotal: 0,
      selected: false,
    };
    setItems((prev) => recalcularCurvaABC([...prev, newItem]));
  };

  const handleSelectAll = () => {
    const newSelectAll = !selectAll;
    setSelectAll(newSelectAll);
    setItems((prev) => prev.map((item) => ({ ...item, selected: newSelectAll })));
  };

  const handleSelectItem = (id: number) => {
    setItems((prev) =>
      prev.map((item) =>
        item.id === id ? { ...item, selected: !item.selected } : item,
      ),
    );
  };

  const formatMoney = (value: number) => {
    return value.toLocaleString("pt-BR", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    });
  };

  const formatMoneyFull = (value: number) => `R$ ${formatMoney(value)}`;

  // Handlers do PDF
  const onDocumentLoadSuccess = ({ numPages }: { numPages: number }) => {
    setNumPages(numPages);
  };

  const buildChildRouteState = (): NovoOrcamentoFlowState => ({
    ...flowState,
    uploadId: resolvedUploadId ?? undefined,
    filename: flowState?.filename ?? pdfFile?.name,
    nomeProjeto: nomeProjetoExport,
    hierarchicalItems,
    structuredData: {
      items: buildItemExportPayload(items),
      hierarchicalItems,
    },
  });

  const handleBack = () => {
    navigate("/analise-orcamento");
  };

  const handleOpenCurvaAbc = () => {
    // Só folhas (sem filhos hierárquicos) — evita 10 + 10.1 + 10.2…
    const payload = items.filter(isExecutivo);
    if (payload.length === 0) {
      toast.warning("Nenhum item disponível", {
        description: "Processe o PDF ou adicione itens à planilha antes de abrir a Curva ABC.",
      });
      return;
    }

    const somaFolhas = payload.reduce(
      (s, i) => s + (Number(i.lineTotal) || Number(i.valorTotalComBdi) || 0),
      0,
    );
    const resumo = flowState?.structuredData?.resumo as
      | {
          valor_total?: number;
          validacao_financeira?: {
            total_geral?: {
              total_geral_documento?: number;
              soma_folhas?: number;
            };
          };
        }
      | undefined;
    const totalOficial = Number(
      resumo?.validacao_financeira?.total_geral?.total_geral_documento ||
        resumo?.valor_total ||
        0,
    );
    const check = detectarDuplaContagemAbc(somaFolhas, totalOficial);
    if (!check.ok) {
      toast.error("Possível dupla contagem na Curva ABC", {
        description: `Soma das folhas (R$ ${somaFolhas.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}) supera o total oficial do orçamento (R$ ${totalOficial.toLocaleString("pt-BR", { minimumFractionDigits: 2 })}). Grupos/subtotais podem estar misturados com serviços.`,
        duration: 12000,
      });
    }

    const uploadId = resolvedUploadId || "upload";
    navigate(`/curva-abc/${uploadId}`, {
      state: {
        ...buildChildRouteState(),
        editedItems: buildItemExportPayload(payload),
        items: buildItemExportPayload(payload),
        abcScope: "folhas_hierarquia",
        orcamentoTotalItens: items.length,
        orcamentoTotalValor: items.reduce(
          (s, i) => s + (Number(i.lineTotal) || Number(i.valorTotalComBdi) || 0),
          0,
        ),
        totalOficialOrcamento: totalOficial > 0 ? totalOficial : undefined,
        somaFolhasAbc: somaFolhas,
      },
    });
  };

  // Handler de Exportação XLSX (Curva ABC)
  const handleExport = async () => {
    if (items.length === 0) {
      toast.warning("Nada para exportar", {
        description: "Adicione pelo menos um item à planilha.",
      });
      return;
    }

    setIsExporting(true);
    try {
      await exportOrcamentoExcel({
        flatItems: buildItemExportPayload(items),
        modelosSelecionados: CURVA_ABC_ONLY,
        nomeProjeto: nomeProjetoExport,
      });
      toast.success("Planilha exportada", {
        description: "O download do XLSX deve iniciar em instantes.",
      });
    } catch (error: unknown) {
      const msg = error instanceof Error ? error.message : "Erro desconhecido";
      console.error("Erro ao exportar:", error);
      toast.error("Falha ao exportar", { description: msg });
    } finally {
      setIsExporting(false);
    }
  };

  const handleFinalizar = async () => {
    const uploadId = resolvedUploadId;
    if (!user?.uid) {
      toast.error("Sessão necessária", { description: "Faça login para finalizar." });
      return;
    }
    if (!uploadId) {
      toast.error("Upload inválido", {
        description: "Reenvie o PDF para gerar um novo orçamento.",
      });
      return;
    }
    if (items.length === 0) {
      toast.warning("Planilha vazia", {
        description: "Adicione pelo menos um item antes de finalizar.",
      });
      return;
    }

    setIsSaving(true);
    try {
      const filename =
        (flowState?.file as File | undefined)?.name ||
        (pdfFile?.name ?? `orcamento-${uploadId}.pdf`);

      const extractedData = (flowState?.extractedData as ExtractedTable[] | undefined) || [];

      const normalizedItems = items.map((item) => ({
        id: String(item.id),
        item: item.item,
        tipo: item.tipo,
        banco: item.banco,
        code: item.code,
        catalogCode: item.catalogCode,
        description: item.description,
        bdi: item.bdi,
        unit: item.unit,
        quantity: item.qty,
        unitValue: item.unitPrice,
        referenceUnitPrice: item.referenceUnitPrice,
        referenceLineTotal: item.referenceLineTotal,
        totalValue:
          item.lineTotal > 0
            ? item.lineTotal
            : Number(item.qty || 0) * Number(item.unitPrice || 0),
        selected: Boolean(item.selected),
        classification: item.classification,
        accumulated_percentage: item.accumulated_percentage,
      }));

      await upsertOrcamento(uploadId, {
        userId: user.uid,
        uploadId,
        filename,
        uploadedAt: new Date(),
        extractedAt: new Date(),
        updatedAt: new Date(),
        items: normalizedItems,
        itemsFound: normalizedItems.length,
        tablesFound: extractedData.length,
        status: "completed",
        errorMessage: null,
      });

      toast.success("Orçamento salvo", {
        description: "Redirecionando para o painel…",
      });
      navigate("/", { replace: true });
    } catch (err: unknown) {
      console.error("Erro ao finalizar orçamento:", err);
      const msg =
        err instanceof Error ? err.message : "Erro ao salvar no Firebase.";
      toast.error("Não foi possível salvar", { description: msg });
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-slate-100 font-sans">
      <ConfirmDialog
        open={deleteItemId !== null}
        title="Remover item?"
        description="Esta linha será excluída da planilha de validação."
        confirmLabel="Remover"
        cancelLabel="Cancelar"
        variant="danger"
        onConfirm={confirmRemoveItem}
        onCancel={() => setDeleteItemId(null)}
      />

      <div className="shrink-0 border-b border-slate-200/80 bg-white/80 px-4 py-4 backdrop-blur-sm sm:px-6">
        <WizardStepper
          steps={ANALISE_ORCAMENTO_WIZARD_STEPS}
          currentStep={ANALISE_ORCAMENTO_VALIDATION_STEP}
        />
      </div>

      <header className="z-20 flex min-h-16 shrink-0 flex-wrap items-center justify-between gap-3 border-b border-slate-200/80 bg-white/95 px-4 py-3 shadow-sm backdrop-blur-sm sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={handleBack}
            className={iconButton}
            aria-label="Voltar para nova análise"
          >
            <ArrowLeft className="h-5 w-5" />
          </button>
          <div className="min-w-0">
            <h1 className="truncate font-display text-2xl font-bold leading-tight text-slate-900">
              Validação — Análise de Orçamento
            </h1>
            <p className="flex flex-wrap items-center gap-1 text-xs text-slate-500">
              <span className="rounded-md bg-violet-100 px-1.5 py-0.5 font-semibold text-violet-800">
                Passo {ANALISE_ORCAMENTO_VALIDATION_STEP}
              </span>
              {isLoading
                ? "Carregando…"
                : `Revise os dados · ${items.length} itens · exporte ou ajuste valores`}
              {pdfFile?.name ? (
                <span className="hidden truncate sm:inline" title={pdfFile.name}>
                  · {pdfFile.name}
                </span>
              ) : null}
            </p>
          </div>
        </div>
        <div className="flex w-full flex-wrap items-center justify-end gap-2 sm:w-auto">
          <button
            type="button"
            disabled={isLoading || items.length === 0 || isExporting}
            className={`${btnMuted} shrink-0`}
            onClick={() => void handleExport()}
            title="Exportar Curva ABC"
          >
            <Download className="h-4 w-4" />
            {isExporting ? "Exportando…" : "Exportar ABC"}
          </button>
          <button
            type="button"
            disabled={isLoading || items.length === 0 || isSaving}
            className={`${btnSuccess} shrink-0`}
            onClick={handleFinalizar}
            title="Salvar orçamento no Firebase"
          >
            <CheckCircle2 className="h-4 w-4" />
            {isSaving ? "Salvando…" : "Finalizar"}
          </button>
          <button
            type="button"
            disabled={isLoading || items.length === 0}
            className={`${btnAccent} shrink-0`}
            onClick={handleOpenCurvaAbc}
            title="Abrir gráfico e classificação ABC de todos os itens"
          >
            <TrendingUp className="h-4 w-4" />
            Curva ABC
          </button>
        </div>
      </header>

      {isReopenedAnalysis && !isLoading && !loadError && items.length > 0 ? (
        <div className="shrink-0 border-b border-slate-200 bg-white px-4 py-3 sm:px-6">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Continuar análise
          </p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={btnAccent} onClick={handleOpenCurvaAbc}>
              <TrendingUp className="h-4 w-4" />
              Curva ABC
            </button>
            <button
              type="button"
              className={btnMuted}
              disabled={isExporting}
              onClick={() => void handleExport()}
            >
              <Download className="h-4 w-4" />
              Exportar ABC
            </button>
          </div>
        </div>
      ) : null}

      <main className="flex min-h-0 flex-1 flex-col overflow-hidden lg:flex-row">
        <div className="flex max-h-[42vh] min-h-0 w-full flex-col border-b border-slate-200 bg-slate-100 lg:max-h-none lg:w-[38%] xl:w-5/12 lg:border-b-0 lg:border-r">
          {/* Toolbar do PDF */}
          <div className="h-12 bg-white border-b border-slate-200 flex items-center justify-between px-4 shrink-0 z-10 gap-2">
            <span className="text-xs font-semibold text-slate-500 uppercase shrink-0">
              {showSelectedTableImages ? "Tabelas analisadas" : "PDF Original"}
            </span>

            <div className="flex items-center gap-2 min-w-0 justify-center flex-1">
              <button
                type="button"
                onClick={() => setScale((s) => Math.max(0.5, s - 0.1))}
                className="cursor-pointer rounded p-1.5 text-slate-600 hover:bg-slate-100"
                title="Diminuir zoom"
                aria-label={showSelectedTableImages ? "Diminuir zoom das imagens" : "Diminuir zoom do PDF"}
              >
                <ZoomOut className="h-4 w-4" />
              </button>
              <span className="text-xs font-mono w-12 text-center text-slate-600">
                {(scale * 100).toFixed(0)}%
              </span>
              <button
                type="button"
                onClick={() => setScale((s) => Math.min(2.0, s + 0.1))}
                className="cursor-pointer rounded p-1.5 text-slate-600 hover:bg-slate-100"
                title="Aumentar zoom"
                aria-label={showSelectedTableImages ? "Aumentar zoom das imagens" : "Aumentar zoom do PDF"}
              >
                <ZoomIn className="h-4 w-4" />
              </button>
              <div className="h-6 border-l border-slate-300 shrink-0" />
              <button
                type="button"
                onClick={() => setScale(showSelectedTableImages ? 1 : 0.8)}
                className="cursor-pointer rounded px-2 py-1.5 text-xs font-medium text-slate-600 hover:bg-slate-100 shrink-0"
                title={showSelectedTableImages ? "Redefinir zoom (100%)" : "Ajustar à largura"}
              >
                {showSelectedTableImages ? "100%" : "Ajustar"}
              </button>
            </div>

            <div className="flex items-center gap-2 shrink-0 justify-end">
              {showSelectedTableImages ? (
                <span className="text-xs text-slate-600 whitespace-nowrap">
                  {selectedTablePreviews.length}{" "}
                  {selectedTablePreviews.length === 1 ? "tabela" : "tabelas"}
                </span>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={() => setPageNumber((p) => Math.max(1, p - 1))}
                    disabled={pageNumber <= 1}
                    className="rounded p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
                    aria-label="Página anterior"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <span className="text-xs text-slate-600">
                    Pág {pageNumber} de {numPages || "--"}
                  </span>
                  <button
                    type="button"
                    onClick={() => setPageNumber((p) => Math.min(numPages, p + 1))}
                    disabled={pageNumber >= numPages}
                    className="rounded p-1.5 text-slate-600 hover:bg-slate-100 disabled:opacity-30"
                    aria-label="Próxima página"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </>
              )}
            </div>
          </div>

          {/* PDF completo ou apenas recortes das tabelas escolhidas */}
          <div className="flex-1 overflow-auto p-4 bg-slate-200/50">
            {showSelectedTableImages ? (
              <div className="mx-auto flex max-w-full flex-col gap-6">
                {selectedTablePreviews.map((tbl) => (
                  <figure
                    key={tbl.id}
                    className="overflow-hidden rounded-lg border border-slate-200 bg-white p-3 shadow-md"
                  >
                    <figcaption className="mb-2 text-xs font-medium text-slate-600">
                      {tbl.name}
                      <span className="font-normal text-slate-400">
                        {" "}
                        · Pág. {tbl.page}
                      </span>
                    </figcaption>
                    <div className="overflow-x-auto rounded border border-slate-100 bg-slate-50">
                      <img
                        src={`data:image/png;base64,${tbl.imagem_base64}`}
                        alt={`Recorte da tabela: ${tbl.name}`}
                        className="mx-auto block h-auto max-w-none"
                        style={{ width: `${Math.round(scale * 100)}%` }}
                        draggable={false}
                      />
                    </div>
                  </figure>
                ))}
              </div>
            ) : pdfFile ? (
              <div className="flex items-start justify-start">
                <Document
                  file={pdfFile}
                  onLoadSuccess={onDocumentLoadSuccess}
                  loading={
                    <div className="flex items-center gap-2 text-slate-500">
                      <Loader2 className="animate-spin w-5 h-5" /> Carregando
                      PDF...
                    </div>
                  }
                  error={
                    <div className="text-red-500 text-sm">
                      Erro ao carregar PDF. Tente enviar novamente.
                    </div>
                  }
                  className="shadow-lg"
                >
                  <Page
                    pageNumber={pageNumber}
                    scale={scale}
                    renderTextLayer={false}
                    renderAnnotationLayer={false}
                    className="bg-white"
                  />
                </Document>
              </div>
            ) : (
              <div className="text-center text-slate-400">
                <AlertCircle className="w-10 h-10 mx-auto mb-2 opacity-50" />
                <p>Nenhum arquivo carregado.</p>
              </div>
            )}
          </div>
        </div>

        {/* --- DIREITA: TABELA EDITÁVEL (Mantida Igual) --- */}
        <div className="flex min-h-0 w-full flex-1 flex-col overflow-hidden bg-white lg:min-w-0">
          {/* Header Tabela */}
          <div className="px-6 py-4 border-b border-slate-100 flex justify-between items-end bg-white shrink-0">
            <div className="flex items-center gap-4">
              <h2 className="text-sm font-semibold text-slate-700 uppercase tracking-wide">
                Planilha Extraída
              </h2>
              <button
                type="button"
                onClick={handleAddItem}
                disabled={isLoading}
                className="rounded px-3 py-1.5 text-xs font-medium text-emerald-700 transition hover:bg-emerald-200 disabled:cursor-not-allowed disabled:opacity-50 bg-emerald-100"
              >
                + Adicionar Item
              </button>
            </div>
          </div>

          {validacaoFinanceiraAlertas.length > 0 && (
            <div className="shrink-0 border-b border-amber-200 bg-amber-50 px-4 py-3 sm:px-6">
              <p className="text-xs font-semibold uppercase tracking-wide text-amber-900">
                Validação financeira
              </p>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm text-amber-900">
                {validacaoFinanceiraAlertas.map((alerta) => (
                  <li key={alerta}>{alerta}</li>
                ))}
              </ul>
            </div>
          )}

          {!duplaContagemAbc.ok && (
            <div className="shrink-0 border-b border-red-200 bg-red-50 px-4 py-3 sm:px-6">
              <p className="text-xs font-semibold uppercase tracking-wide text-red-900">
                Possível dupla contagem na Curva ABC
              </p>
              <p className="mt-1 text-sm text-red-900">
                A soma dos serviços-folha ({formatMoneyFull(abcResumo.totalGeral)})
                supera o total oficial do orçamento (
                {formatMoneyFull(totalOficialOrcamento)}). Grupos ou subtotais
                podem estar sendo somados junto com seus itens filhos.
              </p>
            </div>
          )}

          {estimativoMeta && (
            <div className="shrink-0 border-b border-sky-200 bg-sky-50 px-4 py-3 sm:px-6">
              <p className="text-xs font-semibold uppercase tracking-wide text-sky-900">
                Orçamento estimativo / SRP
              </p>
              <div className="mt-2 grid grid-cols-1 gap-2 text-sm text-sky-950 sm:grid-cols-2 xl:grid-cols-4">
                {typeof estimativoMeta.valor_anual_adotado === "number" && (
                  <p>
                    <span className="text-sky-700">Valor anual adotado: </span>
                    <span className="font-semibold tabular-nums">
                      {formatMoneyFull(estimativoMeta.valor_anual_adotado)}
                    </span>
                  </p>
                )}
                {typeof estimativoMeta.valor_anual_teto_ata === "number" && (
                  <p>
                    <span className="text-sky-700">Teto da ata: </span>
                    <span className="font-semibold tabular-nums">
                      {formatMoneyFull(estimativoMeta.valor_anual_teto_ata)}
                    </span>
                  </p>
                )}
                {typeof estimativoMeta.custo_m2_ponderado === "number" && (
                  <p>
                    <span className="text-sky-700">Custo ponderado: </span>
                    <span className="font-semibold tabular-nums">
                      {formatMoneyFull(estimativoMeta.custo_m2_ponderado)}/m²
                    </span>
                  </p>
                )}
                {typeof estimativoMeta.bdi_percentual === "number" && (
                  <p>
                    <span className="text-sky-700">BDI: </span>
                    <span className="font-semibold tabular-nums">
                      {String(estimativoMeta.bdi_percentual).replace(".", ",")}%
                    </span>
                  </p>
                )}
              </div>
              <p className="mt-2 text-xs text-sky-800">
                O Total Geral abaixo refere-se ao estudo de caso / planilha de serviços extraída
                (ABC). O valor anual estimado é o teto contratual do documento.
              </p>
            </div>
          )}

          {/* Mini dashboard Curva ABC */}
          {!isLoading && !loadError && items.length > 0 && (
            <div className="shrink-0 border-b border-slate-100 bg-slate-50/50 px-4 py-4 sm:px-6">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
                <div className="min-w-0 rounded-xl border border-blue-200 bg-white p-4 shadow-sm">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Total das folhas (ABC)
                  </p>
                  <p
                    className="mt-2 break-words text-base font-bold leading-snug tabular-nums text-blue-700 sm:text-lg"
                    title={formatMoneyFull(abcResumo.totalGeral)}
                  >
                    {formatMoneyFull(abcResumo.totalGeral)}
                  </p>
                  <p className="mt-1 text-xs text-slate-400">
                    {items.filter(isExecutivo).length} serviços-folha ·{" "}
                    {items.length} linhas na planilha
                  </p>
                </div>
                <div className="min-w-0 rounded-xl border border-red-200 bg-red-50/40 p-4 shadow-sm">
                  <p className="text-xs font-semibold uppercase tracking-wide text-red-800">
                    Classe A
                  </p>
                  <p className="mt-2 text-lg font-bold tabular-nums text-red-900">
                    {abcResumo.classeA.count} itens
                  </p>
                  <p
                    className="mt-1 break-words text-sm font-medium leading-snug tabular-nums text-red-700"
                    title={formatMoneyFull(abcResumo.classeA.valor)}
                  >
                    {formatMoneyFull(abcResumo.classeA.valor)}
                  </p>
                </div>
                <div className="min-w-0 rounded-xl border border-yellow-200 bg-yellow-50/50 p-4 shadow-sm">
                  <p className="text-xs font-semibold uppercase tracking-wide text-yellow-900">
                    Classe B
                  </p>
                  <p className="mt-2 text-lg font-bold tabular-nums text-yellow-900">
                    {abcResumo.classeB.count} itens
                  </p>
                  <p
                    className="mt-1 break-words text-sm font-medium leading-snug tabular-nums text-yellow-800"
                    title={formatMoneyFull(abcResumo.classeB.valor)}
                  >
                    {formatMoneyFull(abcResumo.classeB.valor)}
                  </p>
                </div>
                <div className="min-w-0 rounded-xl border border-emerald-200 bg-emerald-50/40 p-4 shadow-sm">
                  <p className="text-xs font-semibold uppercase tracking-wide text-emerald-800">
                    Classe C
                  </p>
                  <p className="mt-2 text-lg font-bold tabular-nums text-emerald-900">
                    {abcResumo.classeC.count} itens
                  </p>
                  <p
                    className="mt-1 break-words text-sm font-medium leading-snug tabular-nums text-emerald-700"
                    title={formatMoneyFull(abcResumo.classeC.valor)}
                  >
                    {formatMoneyFull(abcResumo.classeC.valor)}
                  </p>
                </div>
                <div className="min-w-0 rounded-xl border border-violet-200 bg-violet-50/40 p-4 shadow-sm sm:col-span-2 xl:col-span-1">
                  <p className="text-xs font-semibold uppercase tracking-wide text-violet-800">
                    Economia vs. edital
                  </p>
                  <p
                    className="mt-2 break-words text-base font-bold leading-snug tabular-nums text-violet-900 sm:text-lg"
                    title={formatMoneyFull(economiaTotal)}
                  >
                    {formatMoneyFull(economiaTotal)}
                  </p>
                  <p className="mt-1 text-xs text-violet-700">
                    Diferença quando o preço revisado é menor que o edital
                  </p>
                </div>
              </div>
              <p className="mt-3 text-xs text-slate-500">
                A economia compara o total do edital com o valor revisado (quando menor).
              </p>
              <div className="mt-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
                <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-600">
                  Análise determinística (sem IA)
                </p>
                <AnaliseOrcamentoResumo
                  resultado={resultadoAnalise}
                  uploadId={resolvedUploadId}
                  nomeArquivo={pdfFile?.name ?? (flowState?.filename as string | undefined)}
                  dadosExtraidosPorId={dadosExtraidosPorId}
                  onVerLinha={handleVerLinhaAnalise}
                />
                <p className="mt-3 text-xs text-slate-500">
                  Verifica cálculos, BDI e memória de cálculo nas observações. Grupos, capítulos e
                  subtotais são ignorados automaticamente.
                </p>
              </div>
              {editalSemPrecos ? (
                <div className="mt-4 flex gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-950">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                  <span>
                    Este edital traz a planilha com <strong>preços em branco</strong> (modelo de
                    proposta). Código, descrição, quantidade e BDI foram extraídos do PDF — informe
                    os valores unitários manualmente na tabela.
                  </span>
                </div>
              ) : null}
            </div>
          )}

          {/* Estado: Carregando */}
          {isLoading && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center">
                <Loader2 className="w-8 h-8 animate-spin text-blue-600 mx-auto mb-2" />
                <p className="text-slate-600">Processando dados extraídos...</p>
              </div>
            </div>
          )}

          {/* Estado: Erro */}
          {!isLoading && loadError && (
            <div className="flex-1 flex items-center justify-center">
              <div className="text-center px-6">
                <AlertCircle className="w-10 h-10 text-red-500 mx-auto mb-2" />
                <p className="text-red-600 font-medium">{loadError}</p>
                <button
                  type="button"
                  onClick={() => navigate(-1)}
                  className="mt-4 rounded-lg bg-slate-900 px-4 py-2 text-sm text-white hover:bg-slate-800"
                >
                  Voltar
                </button>
              </div>
            </div>
          )}

          {/* Corpo Tabela com Scroll Independente (vertical + horizontal) */}
          {!isLoading && !loadError && (
            <div className="relative min-h-0 flex-1">
              <div className="absolute inset-0 overflow-auto overscroll-contain pb-16">
              {items.length === 0 ? (
                <div className="flex h-full items-center justify-center text-slate-400">
                  <div className="text-center max-w-md px-4">
                    <AlertCircle className="mx-auto mb-2 h-10 w-10 opacity-50" />
                    <p className="font-medium text-slate-600">
                      {Array.isArray(flowState?.selectedTableIds) &&
                      flowState.selectedTableIds.length > 0
                        ? "Tabela encontrada, mas nenhuma linha pôde ser convertida em item."
                        : "Nenhum item foi extraído do PDF."}
                    </p>
                    {(() => {
                      const diag = (
                        flowState?.structuredData as
                          | {
                              extraction_diagnostics?: Array<{
                                primary_reject_reason?: string;
                                candidate_rows?: number;
                                accepted_items?: number;
                                rejected?: Record<string, number>;
                              }>;
                              ia_metadata?: { details?: unknown };
                            }
                          | undefined
                      )?.extraction_diagnostics;
                      const first = Array.isArray(diag) ? diag[0] : undefined;
                      if (!first) return null;
                      const rejectedTotal = first.rejected
                        ? Object.values(first.rejected).reduce((a, b) => a + b, 0)
                        : 0;
                      return (
                        <p className="mt-2 text-xs text-slate-500">
                          {typeof first.candidate_rows === "number" && (
                            <>
                              {first.candidate_rows} linhas candidatas
                              {rejectedTotal > 0 ? ` · ${rejectedTotal} rejeitadas` : ""}
                              {first.primary_reject_reason
                                ? ` · Motivo principal: ${first.primary_reject_reason}`
                                : ""}
                            </>
                          )}
                        </p>
                      );
                    })()}
                  </div>
                </div>
              ) : (
                <>
                <p className="sticky left-0 top-0 z-30 border-b border-slate-100 bg-white/95 px-4 py-1.5 text-[11px] text-slate-500 backdrop-blur-sm sm:px-6">
                  Role horizontalmente para ver todas as colunas (valores, totais e economia).
                </p>
                <table className="w-max min-w-[1680px] border-collapse text-left text-sm">
                  <thead className="border-b border-slate-200">
                    <tr>
                      <th className="sticky top-8 z-20 w-10 bg-slate-50 px-2 py-3 shadow-[inset_0_-1px_0_#e2e8f0]">
                        <input
                          type="checkbox"
                          checked={selectAll}
                          onChange={handleSelectAll}
                          className="h-4 w-4 cursor-pointer rounded text-blue-600"
                        />
                      </th>
                      <th className="sticky top-8 z-20 w-[7.5rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Cód. ref.
                      </th>
                      <th className="sticky top-8 z-20 w-[6.5rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Banco
                      </th>
                      <th className="sticky top-8 z-20 w-[5.5rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Tipo
                      </th>
                      <th className="sticky top-8 z-20 w-14 whitespace-nowrap bg-slate-50 px-2 py-3 text-center text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        ABC
                      </th>
                      <th className="sticky top-8 z-20 w-28 whitespace-nowrap bg-slate-50 px-2 py-3 text-center text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Análise
                      </th>
                      <th className="sticky top-8 z-20 min-w-[16rem] bg-slate-50 px-3 py-3 text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Descrição
                      </th>
                      <th className="sticky top-8 z-20 w-[6rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        BDI (%)
                      </th>
                      <th className="sticky top-8 z-20 w-16 whitespace-nowrap bg-slate-50 px-2 py-3 text-center text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Un.
                      </th>
                      <th className="sticky top-8 z-20 w-[8.5rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Qtd.
                      </th>
                      <th className="sticky top-8 z-20 w-[10rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        V. unit. s/ BDI
                      </th>
                      <th className="sticky top-8 z-20 w-[11rem] whitespace-nowrap bg-slate-100 px-3 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Total c/ BDI
                      </th>
                      <th className="sticky top-8 z-20 w-[9rem] whitespace-nowrap bg-slate-50 px-3 py-3 text-right text-xs font-semibold uppercase tracking-wider text-slate-500 shadow-[inset_0_-1px_0_#e2e8f0]">
                        Economia
                      </th>
                      <th className="sticky top-8 z-20 w-10 bg-slate-50 px-1 py-3 shadow-[inset_0_-1px_0_#e2e8f0]" />
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {items.map((item) => {
                      const editable = isExecutivo(item);
                      const analiseLinha = analisePorId.get(item.id);
                      const analiseMensagens = mensagensAnaliseLinha(analiseLinha);
                      const alertasCombinados = [
                        ...(item.extractionAlerts ?? []),
                        ...analiseMensagens,
                      ];
                      let rowBgClass = "hover:bg-blue-50/30";
                      if (item.classification === "A") {
                        rowBgClass = "bg-red-50/30 hover:bg-red-50/50";
                      } else if (item.classification === "B") {
                        rowBgClass = "bg-yellow-50/30 hover:bg-yellow-50/50";
                      } else if (item.classification === "C") {
                        rowBgClass = "bg-emerald-50/30 hover:bg-emerald-50/50";
                      }

                      return (
                      <tr
                        key={item.id}
                        id={`orcamento-linha-${item.id}`}
                        className={`group transition ${
                          item.selected ? "bg-blue-50/40" : rowBgClass
                        }`}
                      >
                        <td className="px-2 py-2 align-top">
                          <input
                            type="checkbox"
                            checked={item.selected || false}
                            onChange={() => handleSelectItem(item.id)}
                            className="h-4 w-4 cursor-pointer rounded text-blue-600"
                          />
                        </td>
                        <td className="w-[7.5rem] px-3 py-2 align-top">
                          <input
                            type="text"
                            value={item.code}
                            onChange={(e) =>
                              handleChange(item.id, "code", e.target.value)
                            }
                            title={item.code}
                            className={`w-full bg-transparent font-mono text-xs focus:border-blue-500 focus:outline-none border-b border-transparent ${
                              item.classification === "A"
                                ? "text-red-700"
                                : item.classification === "B"
                                  ? "text-yellow-700"
                                  : item.classification === "C"
                                    ? "text-emerald-700"
                                    : "text-slate-600"
                            }`}
                            placeholder="Ref. edital"
                          />
                        </td>
                        <td className="w-[6.5rem] px-3 py-2 align-top">
                          <input
                            type="text"
                            value={item.banco || ""}
                            onChange={(e) =>
                              handleChange(item.id, "banco", e.target.value)
                            }
                            title={item.banco || undefined}
                            className={`w-full bg-transparent font-mono text-xs focus:border-blue-500 focus:outline-none border-b border-transparent ${
                              item.classification === "A"
                                ? "text-red-700"
                                : item.classification === "B"
                                  ? "text-yellow-700"
                                  : item.classification === "C"
                                    ? "text-emerald-700"
                                    : "text-slate-600"
                            }`}
                            placeholder="SINAPI"
                          />
                        </td>
                        <td className="w-[5.5rem] px-3 py-2 align-top">
                          <select
                            value={item.tipo || "item"}
                            onChange={(e) =>
                              handleChange(item.id, "tipo", e.target.value)
                            }
                            className={`w-full bg-transparent text-xs focus:border-blue-500 focus:outline-none border-b border-transparent ${
                              item.classification === "A"
                                ? "text-red-700"
                                : item.classification === "B"
                                  ? "text-yellow-700"
                                  : item.classification === "C"
                                    ? "text-emerald-700"
                                    : "text-slate-600"
                            }`}
                          >
                            <option value="item">Item</option>
                            <option value="grupo">Grupo</option>
                          </select>
                        </td>
                        <td className="w-14 px-2 py-2 text-center align-top">
                          {item.classification ? (
                            <span
                              className={`inline-flex items-center justify-center rounded-full px-2 py-0.5 text-xs font-bold ${
                                item.classification === "A"
                                  ? "bg-red-100 text-red-700"
                                  : item.classification === "B"
                                    ? "bg-yellow-100 text-yellow-700"
                                    : "bg-emerald-100 text-emerald-700"
                              }`}
                              title={`${item.individual_percentage?.toFixed(2) ?? "0"}% do total · Acumulado: ${item.accumulated_percentage?.toFixed(2) ?? "0"}%`}
                            >
                              {item.classification}
                            </span>
                          ) : (
                            <span className="text-slate-300">-</span>
                          )}
                        </td>
                        <td className="w-28 px-2 py-2 text-center align-top">
                          <AnaliseOrcamentoStatusBadge resultado={analiseLinha} compact />
                        </td>
                        <td className="min-w-[16rem] max-w-[22rem] px-3 py-2 align-top">
                          <div className="flex items-start gap-1">
                            {alertasCombinados.length > 0 ? (
                              <span
                                className="mt-0.5 shrink-0 text-amber-500"
                                title={alertasCombinados.join(" · ")}
                              >
                                <AlertCircle className="h-4 w-4" aria-hidden="true" />
                              </span>
                            ) : null}
                            <textarea
                              rows={2}
                              value={item.description}
                              onChange={(e) =>
                                handleChange(
                                  item.id,
                                  "description",
                                  e.target.value,
                                )
                              }
                              title={item.description}
                              className={`min-h-[2.5rem] w-full resize-y bg-transparent text-sm leading-snug focus:border-blue-500 focus:outline-none border-b border-transparent ${
                                alertasCombinados.length > 0
                                  ? "border-amber-200"
                                  : ""
                              } ${
                                item.classification === "A"
                                  ? "text-red-900"
                                  : item.classification === "B"
                                    ? "text-yellow-900"
                                    : item.classification === "C"
                                      ? "text-emerald-900"
                                      : "text-slate-800"
                              }`}
                            />
                          </div>
                        </td>
                        <td className="w-[6rem] px-3 py-2 align-top">
                          {editable ? (
                            <input
                              type="text"
                              inputMode="decimal"
                              value={item.bdi}
                              onChange={(e) =>
                                handleCellEdit(item.id, "bdi", e.target.value)
                              }
                              title={String(item.bdi)}
                              className={EDITABLE_NUMERIC_CLASS}
                              placeholder="0,00"
                              aria-label={`BDI percentual do item ${item.code}`}
                            />
                          ) : (
                            <span className="block text-right text-sm text-slate-400">—</span>
                          )}
                        </td>
                        <td className="w-16 px-2 py-2 text-center align-top">
                          <input
                            type="text"
                            value={item.unit}
                            onChange={(e) =>
                              handleChange(item.id, "unit", e.target.value)
                            }
                            title={item.unit}
                            className={`w-full rounded px-1 py-1 text-center text-xs font-medium focus:outline-none focus:ring-1 focus:ring-blue-500 ${
                              item.classification === "A"
                                ? "bg-red-100 text-red-800"
                                : item.classification === "B"
                                  ? "bg-yellow-100 text-yellow-800"
                                  : item.classification === "C"
                                    ? "bg-emerald-100 text-emerald-800"
                                    : "bg-slate-50 text-slate-600"
                            }`}
                          />
                        </td>
                        <td className="w-[8.5rem] px-3 py-2 align-top">
                          {editable ? (
                            <input
                              type="text"
                              inputMode="decimal"
                              value={item.qty}
                              onChange={(e) =>
                                handleCellEdit(item.id, "qty", e.target.value)
                              }
                              title={String(item.qty)}
                              placeholder="0"
                              className={EDITABLE_NUMERIC_CLASS}
                              aria-label={`Quantidade do item ${item.code}`}
                            />
                          ) : (
                            <span className="block text-right text-sm text-slate-400">—</span>
                          )}
                        </td>
                        <td className="w-[10rem] px-3 py-2 align-top">
                          {editable ? (
                            <input
                              type="text"
                              inputMode="decimal"
                              value={item.unitPrice}
                              onChange={(e) =>
                                handleCellEdit(item.id, "unitPrice", e.target.value)
                              }
                              title={String(item.unitPrice)}
                              className={EDITABLE_NUMERIC_CLASS}
                              aria-label={`Valor unitário s/ BDI do item ${item.code}`}
                            />
                          ) : (
                            <span className="block text-right text-sm text-slate-400">—</span>
                          )}
                        </td>
                        <td
                          className={`w-[11rem] whitespace-nowrap px-3 py-2 text-right align-top ${
                          item.classification === "A"
                            ? "bg-red-100/50"
                            : item.classification === "B"
                              ? "bg-yellow-100/50"
                              : item.classification === "C"
                                ? "bg-emerald-100/50"
                                : "bg-slate-50/50"
                        }`}
                        >
                          <span
                            className={`block text-sm font-bold tabular-nums ${
                            item.classification === "A"
                              ? "text-red-900"
                              : item.classification === "B"
                                ? "text-yellow-900"
                                : item.classification === "C"
                                  ? "text-emerald-900"
                                  : "text-slate-800"
                          }`}
                            title={formatMoneyFull(item.lineTotal)}
                          >
                            {formatMoneyFull(item.lineTotal)}
                          </span>
                        </td>
                        <td className="w-[9rem] whitespace-nowrap px-3 py-2 text-right align-top">
                          {(() => {
                            const economia = calcularEconomia(item);
                            if (economia <= 0) {
                              return (
                                <span className="text-xs text-slate-300">—</span>
                              );
                            }
                            return (
                              <span
                                className="text-sm font-semibold tabular-nums text-violet-700"
                                title={
                                  item.referenceLineTotal
                                    ? `Ref. edital: ${formatMoneyFull(item.referenceLineTotal)}`
                                    : undefined
                                }
                              >
                                −{formatMoneyFull(economia)}
                              </span>
                            );
                          })()}
                        </td>
                        <td className="w-10 px-1 py-2 text-center align-top">
                          <button
                            type="button"
                            onClick={() => setDeleteItemId(item.id)}
                            className="rounded p-1 text-slate-400 transition hover:bg-red-50 hover:text-red-600"
                            aria-label={`Remover item ${item.code}`}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </td>
                      </tr>
                      );
                    })}
                  </tbody>
                </table>
                </>
              )}
              </div>
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
