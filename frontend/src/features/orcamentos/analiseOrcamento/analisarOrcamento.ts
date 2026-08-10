import { analisarLinhaOrcamento } from "./analisarLinha";
import {
  inferirBdiGlobal,
  mapLinhaAnaliticaToEntrada,
  mapOrcamentoItemToLinhaEntrada,
  mapRawToLinhaOrcamentoEntrada,
  mergeObservacoesNasLinhas,
} from "./mapEntrada";
import { inferirBdisValidosDocumento } from "./tolerancias";
import type { LinhaAnalitica } from "../orcamentoAnalitico";
import type { OrcamentoItem } from "../recalcularCurvaABC";
import {
  ANALISE_ORCAMENTO_VERSAO,
  CONTEXTO_PADRAO,
  type ContextoAnaliseOrcamento,
  type LinhaOrcamentoEntrada,
  type ResultadoAnaliseOrcamento,
  type ResultadoLinhaAnalise,
} from "./types";

function buildResumo(linhas: ResultadoLinhaAnalise[]): ResultadoAnaliseOrcamento["resumo"] {
  const linhasAnalisadas = linhas.filter((linha) => linha.statusGeral !== "ignorado");
  return {
    totalLinhas: linhas.length,
    linhasAnalisadas: linhasAnalisadas.length,
    linhasIgnoradas: linhas.length - linhasAnalisadas.length,
    aprovadas: linhasAnalisadas.filter((linha) => linha.statusGeral === "aprovado").length,
    comAlerta: linhasAnalisadas.filter((linha) => linha.statusGeral === "alerta").length,
    reprovadas: linhasAnalisadas.filter((linha) => linha.statusGeral === "reprovado").length,
  };
}

export function analisarLinhasOrcamento(
  linhasEntrada: LinhaOrcamentoEntrada[],
  contexto?: ContextoAnaliseOrcamento,
): ResultadoAnaliseOrcamento {
  const bdiGlobal =
    contexto?.bdiGlobalPercent && contexto.bdiGlobalPercent > 0
      ? contexto.bdiGlobalPercent
      : inferirBdiGlobal(linhasEntrada);

  const bdisValidos =
    contexto?.bdisValidosDocumento && contexto.bdisValidosDocumento.length > 0
      ? contexto.bdisValidosDocumento
      : inferirBdisValidosDocumento(linhasEntrada);

  const contextoResolvido = {
    bdiGlobalPercent: bdiGlobal,
    bdisValidosDocumento: bdisValidos,
    toleranciaMonetaria: contexto?.toleranciaMonetaria ?? CONTEXTO_PADRAO.toleranciaMonetaria,
    toleranciaPercentual: contexto?.toleranciaPercentual ?? CONTEXTO_PADRAO.toleranciaPercentual,
  };

  // Mesmo universo hierárquico dos cards ABC (pais com filhos ficam ignorados)
  const allItemNumeros = linhasEntrada
    .map((linha) => linha.itemNumero.trim())
    .filter(Boolean);

  const linhas = linhasEntrada.map((linha) =>
    analisarLinhaOrcamento(linha, contextoResolvido, allItemNumeros),
  );

  return {
    versaoModelo: ANALISE_ORCAMENTO_VERSAO,
    contexto: contextoResolvido,
    linhas,
    resumo: buildResumo(linhas),
  };
}

export function analisarOrcamentoFromItens(
  items: OrcamentoItem[],
  rawItems: unknown[] = [],
  contexto?: ContextoAnaliseOrcamento,
): ResultadoAnaliseOrcamento {
  const entradas = mergeObservacoesNasLinhas(
    items.map(mapOrcamentoItemToLinhaEntrada),
    rawItems,
  );
  return analisarLinhasOrcamento(entradas, contexto);
}

export function analisarOrcamentoFromRaw(
  rawItems: unknown[],
  contexto?: ContextoAnaliseOrcamento,
): ResultadoAnaliseOrcamento {
  const entradas = rawItems
    .map((raw, index) => mapRawToLinhaOrcamentoEntrada(raw, index))
    .filter((linha): linha is LinhaOrcamentoEntrada => linha !== null);

  return analisarLinhasOrcamento(entradas, contexto);
}

export function analisarOrcamentoFromLinhasAnaliticas(
  linhas: LinhaAnalitica[],
  rawItems: unknown[] = [],
  contexto?: ContextoAnaliseOrcamento,
): ResultadoAnaliseOrcamento {
  const observacoesPorChave = new Map<string, string>();
  for (const raw of rawItems) {
    const entrada = mapRawToLinhaOrcamentoEntrada(raw, 0);
    if (!entrada?.observacoes.trim()) continue;
    observacoesPorChave.set(`${entrada.itemNumero}|${entrada.codigo}`.toLowerCase(), entrada.observacoes);
    observacoesPorChave.set(entrada.itemNumero.toLowerCase(), entrada.observacoes);
  }

  const entradas = linhas.map((linha) => {
    const observacoes =
      observacoesPorChave.get(`${linha.itemNumero}|${linha.codigo}`.toLowerCase()) ??
      observacoesPorChave.get(linha.itemNumero.toLowerCase()) ??
      "";
    return mapLinhaAnaliticaToEntrada(linha, observacoes);
  });

  return analisarLinhasOrcamento(entradas, contexto);
}

export function resultadoAnalisePorId(
  resultado: ResultadoAnaliseOrcamento,
): Map<string | number, ResultadoLinhaAnalise> {
  const map = new Map<string | number, ResultadoLinhaAnalise>();
  for (const linha of resultado.linhas) {
    map.set(linha.linhaId, linha);
  }
  return map;
}

export function mensagensAnaliseLinha(resultado?: ResultadoLinhaAnalise): string[] {
  if (!resultado || resultado.statusGeral === "ignorado") return [];
  return resultado.verificacoes
    .filter((verificacao) => verificacao.status !== "ok" && verificacao.status !== "nao_aplicavel")
    .map((verificacao) => verificacao.mensagem);
}
