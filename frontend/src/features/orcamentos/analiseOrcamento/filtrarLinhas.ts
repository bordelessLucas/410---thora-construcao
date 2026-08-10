import {
  CABECALHO_COLUNAS_KEYWORDS,
  EXCLUSAO_TIPO_LINHA,
  SUBTOTAL_DESCRICAO_EXATA,
  SUBTOTAL_KEYWORDS,
} from "./constants";
import type { LinhaOrcamentoEntrada } from "./types";
import { isHierarchyLeaf } from "../recalcularCurvaABC";

function normalizeText(value: string): string {
  return value
    .normalize("NFD")
    .replace(/\p{M}/gu, "")
    .toLowerCase()
    .trim();
}

function isAllUppercaseDescription(descricao: string): boolean {
  const letters = descricao.replace(/[^A-Za-zÀ-ÿ]/g, "");
  if (letters.length < 8) return false;
  return descricao === descricao.toUpperCase();
}

function countHeaderKeywordHits(descricao: string): number {
  const normalized = normalizeText(descricao);
  return CABECALHO_COLUNAS_KEYWORDS.filter((keyword) => normalized.includes(keyword)).length;
}

export function isCabecalhoColunas(linha: LinhaOrcamentoEntrada): boolean {
  const descricao = linha.descricao.trim();
  if (!descricao) return false;
  const hits = countHeaderKeywordHits(descricao);
  return (
    hits >= 4 &&
    linha.quantidade <= 0 &&
    linha.precoUnitario <= 0 &&
    !linha.codigo.trim()
  );
}

export function isSubtotal(linha: LinhaOrcamentoEntrada): boolean {
  const descricaoNorm = normalizeText(linha.descricao);
  if (!descricaoNorm) return false;

  if (SUBTOTAL_DESCRICAO_EXATA.has(descricaoNorm)) {
    return linha.quantidade <= 0 && linha.precoUnitario <= 0 && linha.precoTotalComBdi > 0;
  }

  if (SUBTOTAL_KEYWORDS.some((keyword) => descricaoNorm.includes(keyword))) {
    return linha.quantidade <= 0 && linha.precoUnitario <= 0;
  }

  return false;
}

export function isCapitulo(linha: LinhaOrcamentoEntrada): boolean {
  const itemNumero = linha.itemNumero.trim();
  const isIntegerChapter = /^\d+$/.test(itemNumero);
  if (!isIntegerChapter) return false;

  return (
    !linha.codigo.trim() &&
    !linha.banco.trim() &&
    linha.quantidade <= 0 &&
    linha.precoUnitario <= 0 &&
    linha.precoTotalSemBdi <= 0 &&
    linha.precoTotalComBdi <= 0 &&
    isAllUppercaseDescription(linha.descricao)
  );
}

/** Folha tipada como grupo (ex.: 7.5 / 16.1 sem SINAPI) com qtd×VU — analisável/ABC. */
export function isServicoFolhaPrecificado(linha: LinhaOrcamentoEntrada): boolean {
  return (
    linha.quantidade > 0 &&
    linha.precoUnitario > 0 &&
    linha.precoTotalComBdi > 0 &&
    linha.descricao.trim().length > 0
  );
}

export function isGrupoSecao(linha: LinhaOrcamentoEntrada): boolean {
  const tipo = normalizeText(linha.tipoLinha ?? "");

  // Não excluir folhas precificadas só porque a tipagem veio como "grupo"
  if (EXCLUSAO_TIPO_LINHA.has(tipo)) {
    if (isServicoFolhaPrecificado(linha)) return false;
    return true;
  }

  const semDadosFinanceiros =
    linha.quantidade <= 0 &&
    linha.precoUnitario <= 0 &&
    linha.precoTotalSemBdi <= 0 &&
    linha.precoTotalComBdi <= 0;

  if (!semDadosFinanceiros) return false;

  const itemParts = linha.itemNumero.split(".").filter(Boolean);
  const isSectionNumber = itemParts.length <= 2 && linha.itemNumero.length > 0;

  return (
    isSectionNumber &&
    !linha.codigo.trim() &&
    linha.descricao.trim().length > 0 &&
    (isAllUppercaseDescription(linha.descricao) || tipo === "grupo")
  );
}

export function motivoExclusaoLinha(
  linha: LinhaOrcamentoEntrada,
  allItemNumeros?: Iterable<string>,
): string | null {
  if (isCabecalhoColunas(linha)) return "Cabeçalho de colunas";
  if (isSubtotal(linha)) return "Linha de subtotal/totalização";
  if (isCapitulo(linha)) return "Capítulo/categoria";
  // Mesma regra dos cards ABC: pai com filhos = agregador, fora da análise de serviço
  const num = linha.itemNumero.trim();
  if (num && allItemNumeros && !isHierarchyLeaf(num, allItemNumeros)) {
    return "Grupo/agregador hierárquico";
  }
  if (isGrupoSecao(linha)) return "Grupo/seção";
  return null;
}

export function isLinhaAnalisavel(
  linha: LinhaOrcamentoEntrada,
  allItemNumeros?: Iterable<string>,
): boolean {
  if (motivoExclusaoLinha(linha, allItemNumeros)) return false;

  return (
    linha.descricao.trim().length > 0 &&
    linha.unidade.trim().length > 0 &&
    linha.quantidade > 0 &&
    linha.precoUnitario > 0
  );
}
