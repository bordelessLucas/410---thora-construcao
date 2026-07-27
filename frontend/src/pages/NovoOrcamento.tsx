import { useNavigate } from "react-router-dom";
import {
  OrcamentoPdfWizard,
  type OrcamentoWizardResult,
} from "../components/orcamento/OrcamentoPdfWizard";
import { ANALISE_ORCAMENTO_WIZARD_STEPS } from "../features/orcamentos/novoOrcamentoWizard";

export default function NovoOrcamento() {
  const navigate = useNavigate();

  const handleComplete = (result: OrcamentoWizardResult) => {
    navigate(`/validacao/${result.uploadId}`, {
      state: {
        file: result.file,
        uploadId: result.uploadId,
        selectedTableIds: result.selectedTableIds,
        selectedTablePreviews: result.selectedTablePreviews,
        extractedData: result.extractedData,
        structuredData: {
          items: result.structuredItems,
          hierarchicalItems: result.hierarchicalItems,
          resumo: result.resumo,
        },
        hierarchicalItems: result.hierarchicalItems,
        iaMetadata: result.iaMetadata,
        analysisTypes: result.analysisTypes,
      },
    });
  };

  return (
    <div className="mx-auto w-full max-w-5xl px-4 py-6 pb-12 sm:px-6">
      <div className="mb-6">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-thora-steel">
          Thora
        </p>
        <h1 className="page-title mt-1 text-3xl sm:text-3xl">Análise de Orçamento</h1>
        <p className="page-subtitle">
          Envie o PDF, selecione as tabelas e gere a análise completa.
        </p>
      </div>
      <OrcamentoPdfWizard
        steps={ANALISE_ORCAMENTO_WIZARD_STEPS}
        title="Fluxo de análise"
        subtitle={`Passo 1 de ${ANALISE_ORCAMENTO_WIZARD_STEPS.length} — envie o PDF e selecione as tabelas para analisar.`}
        processingLabel="Extraindo itens e montando a análise…"
        logTag="Análise de Orçamento"
        onComplete={handleComplete}
      />
    </div>
  );
}
