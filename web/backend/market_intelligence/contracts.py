"""JSON-safe contracts used by the deterministic Decision Engine v3."""
from __future__ import annotations
from typing import Any

TIMEFRAMES = ("4h", "1h", "30m", "15m", "5m")
FINAL_STATES = {"LONG_READY", "LONG_WAIT", "SHORT_READY", "SHORT_WAIT", "NO_TRADE", "DATA_UNAVAILABLE"}
PROXY_LIMITATION = "All structure, liquidity, FVG, and order-block observations are closed-OHLCV proxies, not institutional certainty."

def num(value: Any) -> float | None:
    try: return float(value)
    except (TypeError, ValueError): return None

def candle_time(candle: dict[str, Any]) -> int | None:
    value = candle.get("close_time", candle.get("open_time", candle.get("timestamp")))
    try: return int(value)
    except (TypeError, ValueError): return None

def evidence(kind: str, direction: str, detail: str, strength: float = 0.0, timeframe: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "direction": direction, "detail": detail, "strength": round(max(0.0, min(1.0, strength)), 3), "timeframe": timeframe, "basis": "OHLCV_PROXY"}
