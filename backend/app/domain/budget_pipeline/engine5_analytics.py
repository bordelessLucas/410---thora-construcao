"""
Engine 5 — Analytics (Curva ABC / Pareto).

100% algorítmico — OpenAI não classifica A/B/C.
"""

from __future__ import annotations

import logging
from typing import Any

from app.domain.abc_curve import build_abc_summary, classify_abc_items

logger = logging.getLogger(__name__)


def run_analytics(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Classifica ABC e devolve (items_classificados, abc_summary, meta)."""
    classified = classify_abc_items(items)
    summary = build_abc_summary(classified)
    meta = {
        "engine": "analytics",
        "algorithm": summary.get("algorithm") or "pareto_before_item_80_95",
        "total_items": summary.get("total_items"),
        "total_value": summary.get("total_value"),
    }
    logger.info(
        "[engine5] ABC items=%s total=%s A=%s B=%s C=%s",
        summary.get("total_items"),
        summary.get("total_value"),
        (summary.get("class_a") or {}).get("count"),
        (summary.get("class_b") or {}).get("count"),
        (summary.get("class_c") or {}).get("count"),
    )
    return classified, summary, meta
