"""
Parser monetário BRL canônico e contrato de preços (S/BDI × C/BDI).

Regras:
- Separador de milhar: `.`  |  decimal: `,`  (ex.: 1.234,56)
- Só `.` com 3 dígitos no final → milhar BR (1.234 → 1234)
- Só `.` com 1–2 dígitos no final → decimal US (12.34 → 12.34)
- ABC e totais econômicos usam sempre valor_total_com_bdi
"""

from __future__ import annotations

import re
from typing import Any

_CURRENCY_RE = re.compile(r"[Rr]\$|\s")
_PERCENT_RE = re.compile(r"%")


def parse_brl(value: Any) -> float:
    """Converte número em formato brasileiro (ou híbrido) para float."""
    if value is None or value == "":
        return 0.0
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return float(value) if value == value else 0.0  # NaN guard

    text = _CURRENCY_RE.sub("", str(value).strip())
    text = _PERCENT_RE.sub("", text).strip()
    if not text or text in {".", ",", "-", "+", "—"}:
        return 0.0

    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative = True
        text = text[1:-1].strip()
    if text.startswith("-"):
        negative = True
        text = text[1:].strip()

    # 1.234,56 → BR completo
    if "." in text and "," in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            # raro: 1,234.56
            text = text.replace(",", "")
    elif "," in text:
        # Só vírgula: sempre decimal BR.
        # Coeficientes SINAPI usam muitas casas (0,0006000 / 1,0000000).
        # Nunca remover a vírgula (isso transformava 1,0000000 → 10_000_000).
        parts = text.split(",")
        if len(parts) == 2 and parts[1].isdigit():
            text = f"{parts[0]}.{parts[1]}"
        else:
            text = text.replace(",", ".")
    elif "." in text:
        parts = text.split(".")
        # 1.234.567 (só milhares)
        if len(parts) > 2 and all(p.isdigit() for p in parts):
            text = "".join(parts)
        # 1.234 → milhar BR (exatamente 3 dígitos após o ponto)
        elif (
            len(parts) == 2
            and parts[0].isdigit()
            and parts[1].isdigit()
            and len(parts[1]) == 3
            and len(parts[0]) <= 3
        ):
            text = parts[0] + parts[1]
        # 12.34 → decimal
        else:
            pass

    try:
        number = float(text)
    except (TypeError, ValueError):
        return 0.0
    return -number if negative else number


def sanitize_bdi_percent(value: Any) -> float:
    raw = parse_brl(value)
    if 0 < raw <= 100:
        return round(raw, 4)
    return 0.0


def infer_bdi_percent(
    quantidade: float,
    valor_unitario_sem_bdi: float,
    valor_total_com_bdi: float,
) -> float:
    if quantidade <= 0 or valor_unitario_sem_bdi <= 0 or valor_total_com_bdi <= 0:
        return 0.0
    base = quantidade * valor_unitario_sem_bdi
    if base <= 0:
        return 0.0
    if valor_total_com_bdi <= base * 1.001:
        return 0.0
    inferred = (valor_total_com_bdi / base - 1.0) * 100.0
    if 0 < inferred <= 100:
        return round(inferred, 2)
    return 0.0


def relative_error(expected: float, actual: float) -> float:
    denom = max(abs(expected), abs(actual), 1e-9)
    return abs(expected - actual) / denom


def resolve_pricing_contract(
    *,
    quantidade: Any = 0,
    bdi: Any = 0,
    valor_unitario: Any = 0,
    valor_total: Any = 0,
    valor_unitario_sem_bdi: Any = None,
    valor_unitario_com_bdi: Any = None,
    valor_total_sem_bdi: Any = None,
    valor_total_com_bdi: Any = None,
    tolerance: float = 0.02,
) -> dict[str, Any]:
    """
    Normaliza preços para o contrato canônico.

    Retorna:
      quantidade, bdi,
      valor_unitario_sem_bdi, valor_unitario_com_bdi,
      valor_total_sem_bdi, valor_total_com_bdi,
      valor_unitario (= sem_bdi, compat),
      valor_total (= com_bdi, fonte ABC),
      quarentena, alertas, confianca_preco
    """
    qty = parse_brl(quantidade)
    alerts: list[str] = []

    vu_raw = parse_brl(valor_unitario)
    vt_raw = parse_brl(valor_total)
    vu_sem = parse_brl(valor_unitario_sem_bdi) if valor_unitario_sem_bdi is not None else 0.0
    vu_com = parse_brl(valor_unitario_com_bdi) if valor_unitario_com_bdi is not None else 0.0
    vt_sem = parse_brl(valor_total_sem_bdi) if valor_total_sem_bdi is not None else 0.0
    vt_com = parse_brl(valor_total_com_bdi) if valor_total_com_bdi is not None else 0.0

    if vt_com <= 0 and vt_raw > 0:
        vt_com = vt_raw

    # Linha zerada do edital: qty=0 e total=0 com VU de referência → preservar
    if qty <= 0 and vt_com <= 0 and vt_raw <= 0:
        vu_hint = vu_raw if vu_raw > 0 else (vu_sem if vu_sem > 0 else vu_com)
        if vu_hint > 0 or vu_sem > 0 or vu_com > 0:
            bdi_pct = sanitize_bdi_percent(bdi)
            factor = 1.0 + bdi_pct / 100.0 if bdi_pct > 0 else 1.0
            if vu_sem <= 0 and vu_com <= 0 and vu_hint > 0:
                vu_sem = vu_hint
            if vu_com <= 0 and vu_sem > 0:
                vu_com = vu_sem * factor
            if vu_sem <= 0 and vu_com > 0:
                vu_sem = vu_com / factor if factor > 0 else vu_com
            return {
                "quantidade": 0.0,
                "bdi": round(bdi_pct, 4),
                "valor_unitario_sem_bdi": round(vu_sem, 6),
                "valor_unitario_com_bdi": round(vu_com, 6),
                "valor_total_sem_bdi": 0.0,
                "valor_total_com_bdi": 0.0,
                "valor_unitario": round(vu_sem, 6),
                "valor_total": 0.0,
                "quarentena": False,
                "alertas_preco": ["Linha com quantidade/total zero — fora da Curva ABC"],
                "confianca_preco": 1.0,
            }

    # Corrige total colado na quantidade (falha clássica de extração ABC)
    vu_hint = vu_raw if vu_raw > 0 else (vu_sem if vu_sem > 0 else vu_com)
    if (
        qty > 1
        and vu_hint > 0
        and vt_com > 0
        and abs(vt_com - qty) <= max(0.01, abs(qty) * 1e-9)
    ):
        expected = qty * vu_hint
        if abs(expected - vt_com) > max(1.0, abs(expected) * 0.02):
            vt_com = expected
            vt_raw = expected
            alerts.append("Total igual à quantidade — recalculado como Qtd×VU")

    if vu_sem <= 0 and vu_com <= 0 and vu_raw > 0:
        # Ambiguity: detect S/BDI vs C/BDI against total
        bdi_hint = sanitize_bdi_percent(bdi)
        if qty > 0 and vt_com > 0:
            err_as_com = relative_error(qty * vu_raw, vt_com)
            err_as_sem = relative_error(
                qty * vu_raw * (1 + bdi_hint / 100.0),
                vt_com,
            ) if bdi_hint > 0 else 1.0
            # Also try inferring BDI treating VU as sem
            if bdi_hint <= 0:
                inferred = infer_bdi_percent(qty, vu_raw, vt_com)
                err_as_sem_inf = relative_error(
                    qty * vu_raw * (1 + inferred / 100.0),
                    vt_com,
                ) if inferred > 0 else 1.0
            else:
                inferred = 0.0
                err_as_sem_inf = 1.0

            if (err_as_com <= tolerance and err_as_com <= err_as_sem):
                vu_com = vu_raw
                bdi = 0
                alerts.append("VU interpretado como C/BDI (bate com total)")
            elif err_as_sem <= tolerance or err_as_sem_inf <= tolerance:
                vu_sem = vu_raw
                if bdi_hint <= 0 and inferred > 0:
                    bdi = inferred
            else:
                # Prefer VT as ground truth; derive VU sem if possible
                vu_sem = vu_raw
                alerts.append("VU/total inconsistentes — VT do PDF prevalece")
        else:
            vu_sem = vu_raw

    bdi_pct = sanitize_bdi_percent(bdi)
    if bdi_pct <= 0 and qty > 0 and vu_sem > 0 and vt_com > 0:
        bdi_pct = infer_bdi_percent(qty, vu_sem, vt_com)

    factor = 1.0 + bdi_pct / 100.0 if bdi_pct > 0 else 1.0

    if vu_sem <= 0 and vu_com > 0:
        vu_sem = vu_com / factor if factor > 0 else vu_com
    if vu_com <= 0 and vu_sem > 0:
        vu_com = vu_sem * factor

    if vt_sem <= 0 and qty > 0 and vu_sem > 0:
        vt_sem = qty * vu_sem
    if vt_com <= 0 and qty > 0 and vu_com > 0:
        vt_com = qty * vu_com
    elif vt_com <= 0 and vt_sem > 0:
        vt_com = vt_sem * factor

    if vu_sem <= 0 and vt_sem > 0 and qty > 0:
        vu_sem = vt_sem / qty
    if vu_com <= 0 and vt_com > 0 and qty > 0:
        vu_com = vt_com / qty
    if vt_sem <= 0 and vt_com > 0 and factor > 0:
        vt_sem = vt_com / factor

    quarantine = False
    price_confidence = 1.0

    if qty > 0 and vu_com > 0 and vt_com > 0:
        err = relative_error(qty * vu_com, vt_com)
        if err > tolerance:
            # Total do edital é a fonte da ABC — ajusta VU e NÃO quarentena
            expected_vu = vt_com / qty
            alerts.append(
                f"Qtd×VU≠VT (erro {err:.1%}) — total do edital usado na ABC"
            )
            price_confidence = max(0.45, 1.0 - min(err, 0.55))
            vu_com = expected_vu
            vu_sem = vu_com / factor if factor > 0 else vu_com
            vt_sem = qty * vu_sem
    elif vt_com <= 0 and qty > 0 and vu_sem <= 0 and vu_com <= 0:
        quarantine = True
        price_confidence = 0.2
        alerts.append("Sem quantidade nem preços")
    elif vt_com <= 0 and (vu_sem > 0 or qty > 0):
        price_confidence = 0.55
        alerts.append("Total c/BDI ausente")
    elif qty <= 0 and vt_com <= 0 and vu_sem <= 0:
        quarantine = True
        price_confidence = 0.2
        alerts.append("Sem quantidade nem preços")

    return {
        "quantidade": round(qty, 6),
        "bdi": round(bdi_pct, 4),
        "valor_unitario_sem_bdi": round(vu_sem, 6),
        "valor_unitario_com_bdi": round(vu_com, 6),
        "valor_total_sem_bdi": round(vt_sem, 2),
        "valor_total_com_bdi": round(vt_com, 2),
        # Compatibilidade com consumidores existentes:
        "valor_unitario": round(vu_sem, 6),
        "valor_total": round(vt_com, 2),
        "quarentena": quarantine,
        "alertas_preco": alerts,
        "confianca_preco": round(price_confidence, 3),
    }
