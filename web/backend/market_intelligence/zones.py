from __future__ import annotations
from typing import Any

def setup_zone(candles: list[dict[str, Any]], direction: str, assessment: dict[str, Any]) -> dict[str, Any]:
    for c in reversed(candles[:-1]):
        opposing=float(c["close"])<float(c["open"]) if direction=="LONG" else float(c["close"])>float(c["open"])
        if opposing: return {"low":float(c["low"]),"high":float(c["high"]),"source":"last_opposing_closed_candle","basis":"OHLCV_PROXY"}
    low,high=assessment["swing_low"],assessment["swing_high"]; return {"low":low+(high-low)*.382,"high":low+(high-low)*.618,"source":"retracement_fallback","basis":"OHLCV_PROXY"}
