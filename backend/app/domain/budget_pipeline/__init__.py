"""
Pipeline determinístico de orçamentos: parser por coordenadas ANTES da IA.

Engines:
  1. LayoutAnalyzer   — texto + (x,y,w,h,página,fonte)
  2. TableReconstruction — linhas Y, colunas X, descrições, hierarquia
  3. FinancialValidator  — subtotais + Total Geral (tol. R$ 0,01)
  4. Normalizer          — JSON canônico
  5. Analytics           — Curva ABC / Pareto (sem OpenAI)
"""

from __future__ import annotations

from app.domain.budget_pipeline.runner import PipelineRejectedError, run_pipeline

__all__ = ["run_pipeline", "PipelineRejectedError"]
