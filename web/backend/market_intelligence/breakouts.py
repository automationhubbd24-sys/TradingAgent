from __future__ import annotations
from typing import Any

def detect(candles: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    event=state.get("bos") or state.get("choch")
    if not event: return {"state":"NONE","basis":"OHLCV_PROXY"}
    c=candles[-1]; level=event["level"]; direction=event["direction"]; retest=(float(c["low"])<=level<=float(c["close"]) if direction=="LONG" else float(c["high"])>=level>=float(c["close"]))
    failed=(float(c["close"])<level if direction=="LONG" else float(c["close"])>level)
    return {"state":"FALSE_BREAKOUT" if failed else "RETEST" if retest else "BREAKOUT","direction":direction,"level":level,"close_confirmed":True,"basis":"OHLCV_PROXY"}
