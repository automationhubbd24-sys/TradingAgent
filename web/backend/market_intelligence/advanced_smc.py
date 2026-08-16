"""Deterministic, closed-candle SMC observations for plain market snapshots."""
from __future__ import annotations

from typing import Any

from .breakouts import detect as detect_breakout
from .fvg import detect as detect_fvg
from .liquidity import liquidity_map, sr_clusters
from .order_blocks import detect as detect_order_blocks
from .structure import assess


def _order_block_quality(block: dict[str, Any], displacement: dict[str, Any]) -> dict[str, Any]:
    grade = displacement.get("grade", "NONE")
    score = {"NONE": 0.2, "WEAK": 0.4, "MODERATE": 0.65, "STRONG": 0.82, "EXTREME": 1.0}.get(grade, 0.2)
    if block["state"] == "FRESH":
        score += 0.1
    elif block["state"] == "INVALIDATED":
        score = 0.0
    return {
        **block,
        "quality": round(min(score, 1.0), 3),
        "quality_grade": "HIGH" if score >= 0.8 else "MEDIUM" if score >= 0.55 else "LOW",
    }


def analyze(candles: list[dict[str, Any]], timeframe: str = "unknown") -> dict[str, Any]:
    """Return compact SMC contracts using only supplied, closed OHLCV candles."""
    structure = assess(candles, timeframe)
    if structure["structure"] == "INSUFFICIENT":
        return {
            "timeframe": timeframe,
            "basis": "OHLCV_PROXY",
            "status": "INSUFFICIENT_DATA",
            "confirmed_swings": structure["pivots"],
            "bos": None,
            "choch": None,
            "displacement": None,
            "order_blocks": [],
            "fvg": [],
            "support_resistance": [],
            "breakout": {"state": "NONE", "basis": "OHLCV_PROXY"},
            "liquidity": {"levels": [], "equal_levels": [], "sweep": None, "basis": "OHLCV_PROXY"},
        }

    order_blocks = [
        _order_block_quality(block, structure["displacement"])
        for block in detect_order_blocks(candles, structure)
    ]
    return {
        "timeframe": timeframe,
        "basis": "OHLCV_PROXY",
        "status": "READY",
        "bias": structure["bias"],
        "confirmed_swings": structure["pivots"],
        "bos": structure["bos"],
        "choch": structure["choch"],
        "displacement": structure["displacement"],
        "order_blocks": order_blocks,
        "fvg": detect_fvg(candles),
        "support_resistance": sr_clusters(candles, structure),
        "breakout": detect_breakout(candles, structure),
        "liquidity": liquidity_map(candles, structure),
    }


def analyze_snapshot(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Analyze every candle series in a plain snapshot keyed by timeframe."""
    return {
        timeframe: analyze(candles, timeframe)
        for timeframe, candles in snapshot.get("candles", {}).items()
        if isinstance(candles, list)
    }
