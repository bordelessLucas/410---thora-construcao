import { isLinhaAnalisavel, motivoExclusaoLinha } from "./filtrarLinhas";
import { analisarMemoriaCalculo } from "./memoriaCalculo";
import {
  inferirBdisValidosDocumento,
  linhaBdiConfereDocumento,
  toleranciaMonetariaEfetiva,
} from "./tolerancias";
import {
  ANALISE_ORCAMENTO_VERSAO,
  CONTEXTO_PADRAO,
  type ContextoAnaliseOrcamento,
  type LinhaOrcamentoEntrada,
  type ResultadoLinhaAnalise,
  type VerificacaoLinha,
} from "./types";

function roundMoney(value: number): number {
  return Math.round(value * 100) / 100;
}

function resolveContexto(contexto?: ContextoAnaliseOrcamento) {
  return {
    bdiGlobalPercent: contexto?.bdiGlobalPercent ?? CONTEXTO_PADRAO.bdiGlobalPercent,
    bdisValidosDocumento:
      contexto?.bdisValidosDocumento ?? CONTEXTO_PADRAO.bdisValidosDocumento,
    toleranciaMonetaria: contexto?.toleranciaMonetaria ?? CONTEXTO_PADRAO.toleranciaMonetaria,
    toleranciaPercentual: contexto?.toleranciaPercentual ?? CONTEXTO_PADRAO.toleranciaPercentual,
  };
}

function compararValores(
  calculado: number,
  informado: number,
  toleranciaMonetaria: number,
): { ok: boolean; diferenca: number } {
  const diferenca = Math.abs(calculado - informado);
  return { ok: diferenca <= toleranciaMonetaria, diferenca };
}

function statusGeralFromVerificacoes(verificacoes: VerificacaoLinha[]): ResultadoLinhaAnalise["statusGeral"] {
  const hasErro = verificacoes.some(
    (verificacao) =>
      verificacao.severidade === "erro" &&
      (verificacao.status === "divergente" || verificacao.status === "alerta"),
  );
  if (hasErro) return "reprovado";

  const hasAlerta = verificacoes.some(
    (verificacao) =>
      verificacao.severidade === "alerta" &&
      (verificacao.status === "divergente" || verificacao.status === "alerta"),
  );
  if (hasAlerta) return "alerta";

  return "aprovado";
}

export function analisarLinhaOrcamento(
  linha: LinhaOrcamentoEntrada,
  contexto?: ContextoAnaliseOrcamento,
  allItemNumeros?: Iterable<string>,
): ResultadoLinhaAnalise {
  const resolved = resolveContexto(contexto);
  const motivoIgnorado = motivoExclusaoLinha(linha, allItemNumeros);

  if (motivoIgnorado || !isLinhaAnalisavel(linha, allItemNumeros)) {
    return {
      linhaId: linha.id,
      itemNumero: linha.itemNumero,
      descricao: linha.descricao,
      statusGeral: "ignorado",
      motivoIgnorado: motivoIgnorado ?? "Linha sem dados suficientes para análise",
      verificacoes: [],
    };
  }

  const verificacoes: VerificacaoLinha[] = [];

  const camposFaltantes: string[] = [];
  if (!linha.descricao.trim()) camposFaltantes.push("descrição");
  if (!linha.unidade.trim()) camposFaltantes.push("unidade");
  if (linha.quantidade <= 0) camposFaltantes.push("quantidade");
  if (linha.precoUnitario <= 0) camposFaltantes.push("preço unitário");

  verificacoes.push({
    regraId: "CAMPOS_OBRIGATORIOS",
    status: camposFaltantes.length === 0 ? "ok" : "divergente",
    severidade: camposFaltantes.length === 0 ? "info" : "erro",
    mensagem:
      camposFaltantes.length === 0
        ? "Campos obrigatórios preenchidos."
        : `Campos obrigatórios ausentes: ${camposFaltantes.join(", ")}.`,
  });

  const subtotalCalculado = roundMoney(linha.quantidade * linha.precoUnitario);
  const subtotalInformado =
    linha.precoTotalSemBdi > 0 ? linha.precoTotalSemBdi : subtotalCalculado;
  const subtotalCheck = compararValores(
    subtotalCalculado,
    subtotalInformado,
    toleranciaMonetariaEfetiva(subtotalInformado, resolved.toleranciaMonetaria),
  );

  verificacoes.push({
    regraId: "CALC_SUBTOTAL",
    status: subtotalCheck.ok ? "ok" : "divergente",
    severidade: subtotalCheck.ok ? "info" : "erro",
    valorCalculado: subtotalCalculado,
    valorInformado: subtotalInformado,
    diferenca: roundMoney(subtotalCheck.diferenca),
    mensagem: subtotalCheck.ok
      ? `${linha.quantidade} × ${linha.precoUnitario} = ${subtotalCalculado}`
      : `Subtotal divergente: esperado ${subtotalCalculado}, informado ${subtotalInformado}.`,
  });

  // Sintético / planilha com VU já c/BDI: qty×VU ≈ total c/BDI → BDI embutido
  const totalComBdiInformado =
    linha.precoTotalComBdi > 0 ? linha.precoTotalComBdi : 0;
  const pricesAlreadyIncludeBdi =
    totalComBdiInformado > 0 &&
    compararValores(
      subtotalCalculado,
      totalComBdiInformado,
      toleranciaMonetariaEfetiva(totalComBdiInformado, resolved.toleranciaMonetaria),
    ).ok;

  const bdiEfetivo = pricesAlreadyIncludeBdi ? 0 : linha.bdiPercent;

  if (bdiEfetivo > 0) {
    const totalComBdiCalculado = roundMoney(subtotalInformado * (1 + bdiEfetivo / 100));
    const totalInformado =
      linha.precoTotalComBdi > 0 ? linha.precoTotalComBdi : totalComBdiCalculado;
    const bdiCheck = compararValores(
      totalComBdiCalculado,
      totalInformado,
      toleranciaMonetariaEfetiva(totalInformado, resolved.toleranciaMonetaria),
    );

    verificacoes.push({
      regraId: "CALC_BDI",
      status: bdiCheck.ok ? "ok" : "divergente",
      severidade: bdiCheck.ok ? "info" : "erro",
      valorCalculado: totalComBdiCalculado,
      valorInformado: totalInformado,
      diferenca: roundMoney(bdiCheck.diferenca),
      mensagem: bdiCheck.ok
        ? `${subtotalInformado} × (1 + ${bdiEfetivo}%) = ${totalComBdiCalculado}`
        : `Total c/ BDI divergente: esperado ${totalComBdiCalculado}, informado ${totalInformado}.`,
    });

    const deveVerificarBdiDocumento =
      resolved.bdiGlobalPercent > 0 || resolved.bdisValidosDocumento.length > 0;

    if (deveVerificarBdiDocumento) {
      const bdiGlobalCheck = linhaBdiConfereDocumento(
        bdiEfetivo,
        resolved.bdisValidosDocumento,
        resolved.bdiGlobalPercent,
        resolved.toleranciaPercentual,
      );

      const bdisLabel =
        resolved.bdisValidosDocumento.length > 1
          ? resolved.bdisValidosDocumento.map((b) => `${b}%`).join(", ")
          : `${resolved.bdiGlobalPercent}%`;

      verificacoes.push({
        regraId: "BDI_GLOBAL",
        status: bdiGlobalCheck ? "ok" : "alerta",
        severidade: bdiGlobalCheck ? "info" : "alerta",
        valorCalculado: resolved.bdiGlobalPercent,
        valorInformado: bdiEfetivo,
        diferenca: roundMoney(Math.abs(bdiEfetivo - resolved.bdiGlobalPercent)),
        mensagem: bdiGlobalCheck
          ? resolved.bdisValidosDocumento.length > 1
            ? `BDI ${bdiEfetivo}% confere com os BDIs do documento (${bdisLabel}).`
            : `BDI ${bdiEfetivo}% confere com o BDI predominante (${bdisLabel}).`
          : `BDI da linha (${bdiEfetivo}%) não corresponde aos BDIs do documento (${bdisLabel}).`,
      });
    }
  } else {
    verificacoes.push({
      regraId: "CALC_BDI",
      status: "nao_aplicavel",
      severidade: "info",
      mensagem: pricesAlreadyIncludeBdi
        ? "Preços já incluem BDI (Qtd × VU ≈ Total c/BDI)."
        : "BDI não informado na linha.",
    });
  }

  let memoriaCalculo;
  if (linha.observacoes.trim()) {
    memoriaCalculo =
      analisarMemoriaCalculo(
        linha.observacoes,
        linha.quantidade,
        linha.unidade,
        resolved.toleranciaMonetaria,
      ) ?? undefined;

    if (memoriaCalculo) {
      verificacoes.push({
        regraId: "MEMORIA_CALCULO",
        status: memoriaCalculo.bateComQuantidade ? "ok" : "alerta",
        severidade: memoriaCalculo.bateComQuantidade ? "info" : "alerta",
        valorCalculado: memoriaCalculo.resultadoExtraido ?? undefined,
        valorInformado: linha.quantidade,
        diferenca:
          memoriaCalculo.resultadoExtraido != null
            ? roundMoney(Math.abs(memoriaCalculo.resultadoExtraido - linha.quantidade))
            : undefined,
        mensagem: memoriaCalculo.explicacao,
      });
    } else {
      verificacoes.push({
        regraId: "MEMORIA_CALCULO",
        status: "nao_aplicavel",
        severidade: "info",
        mensagem: "Observações presentes, mas sem padrão numérico reconhecível.",
      });
    }
  }

  return {
    linhaId: linha.id,
    itemNumero: linha.itemNumero,
    descricao: linha.descricao,
    statusGeral: statusGeralFromVerificacoes(verificacoes),
    verificacoes,
    memoriaCalculo,
  };
}

export function getAnaliseOrcamentoVersao(): string {
  return ANALISE_ORCAMENTO_VERSAO;
}
