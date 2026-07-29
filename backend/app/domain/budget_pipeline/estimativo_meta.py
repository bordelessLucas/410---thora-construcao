"""
Metadados de orçamentos estimativos / SRP (sem quebrar planilha sintética).

Detecta sinais genéricos (valor anual, teto da ata, praça-modelo) e extrai
números oficiais da capa — independente de cliente/modelo cadastrado.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pdfplumber

from app.domain.money import parse_brl

_ESTIMATIVO_HINT = re.compile(
    r"valor\s+anual\s+estimado|teto\s+da\s+ata|pra[cç]a-modelo|"
    r"or[cç]amento\s+estimativo\s*[—\-–:]",
    re.IGNORECASE,
)

_VALOR_ANUAL_ADOTADO = re.compile(
    r"(?:cen[aá]rio\s+adotado|valor\s+anual\s+estimado\s*\(teto\s+da\s+ata\)|"
    r"valor\s+anual\s+estimado\s+da\s+contrata[cç][aã]o)"
    r"[^\d]{0,80}R\$\s*([\d.]+,\d{2})",
    re.IGNORECASE,
)
_VALOR_ANUAL_PISO = re.compile(
    r"(?:cen[aá]rio\s+piso|valor\s+anual\s+estimado\s*[—\-–]?\s*cen[aá]rio\s+piso)"
    r"[^\d]{0,80}R\$\s*([\d.]+,\d{2})",
    re.IGNORECASE,
)
_VALOR_TETO_ATA = re.compile(
    r"valor\s+anual\s+estimado\s*[—\-–]\s*teto\s+da\s+ata"
    r"[^\d]{0,60}R\$\s*([\d.]+,\d{2})",
    re.IGNORECASE,
)
_BDI = re.compile(
    r"BDI\s+(?:adotado|resultante)?\s*:?\s*([\d]+,\d{2})\s*%",
    re.IGNORECASE,
)
_CUSTO_M2 = re.compile(
    r"(?:custo\s+(?:unit[aá]rio\s+)?ponderado|pre[cç]o\s*\(R\$/m)"
    r"[^\d]{0,40}(?:R\$\s*)?([\d.]+,\d{2})",
    re.IGNORECASE,
)
_AREA_MODELO = re.compile(
    r"[ÁA]rea\s+(?:da\s+)?pra[cç]a-modelo\s*(?:\(m[²2]\))?\s*:?\s*([\d.]+,\d{2})",
    re.IGNORECASE,
)
_PONDERADO_M2 = re.compile(
    r"PONDERADO\s+100,0%\s+[\d.]+,\d{2}\s+([\d.]+,\d{2})",
    re.IGNORECASE,
)


def detect_estimativo_document(text_blob: str) -> bool:
    """True só com sinais de capa estimativa (evita falso positivo em menções soltas)."""
    blob = text_blob or ""
    return bool(
        re.search(r"valor\s+anual\s+estimado", blob, re.I)
        or re.search(r"teto\s+da\s+ata", blob, re.I)
        or re.search(r"pra[cç]a-modelo", blob, re.I)
        or re.search(r"or[cç]amento\s+estimativo\s*[—\-–:]", blob, re.I)
    )


def extract_estimativo_metadata(pdf_path: Path | str, *, max_pages: int = 8) -> dict[str, Any]:
    """
    Lê as primeiras páginas e extrai metadados de orçamento estimativo/SRP.

    Retorna {} se o PDF não parecer estimativo.
    """
    path = Path(pdf_path)
    chunks: list[str] = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages[: max(1, min(max_pages, len(pdf.pages)))]:
            try:
                chunks.append(page.extract_text() or "")
            except Exception:
                continue
    blob = "\n".join(chunks)
    if not detect_estimativo_document(blob):
        return {}

    def _first(pattern: re.Pattern[str]) -> float | None:
        m = pattern.search(blob)
        if not m:
            return None
        v = parse_brl(m.group(1))
        return v if v > 0 else None

    custo_m2 = _first(_PONDERADO_M2) or _first(_CUSTO_M2)
    meta: dict[str, Any] = {
        "document_kind": "estimativo_srp",
        "valor_anual_adotado": _first(_VALOR_ANUAL_ADOTADO),
        "valor_anual_piso": _first(_VALOR_ANUAL_PISO),
        "valor_anual_teto_ata": _first(_VALOR_TETO_ATA),
        "bdi_percentual": _first(_BDI),
        "custo_m2_ponderado": custo_m2,
        "area_praca_modelo_m2": _first(_AREA_MODELO),
    }
    # Remove nulos para payload limpo
    return {k: v for k, v in meta.items() if v is not None}
