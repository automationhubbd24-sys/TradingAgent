from __future__ import annotations
from typing import Any

def detect(candles: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    event=state.get("bos") or state.get("choch"); disp=state.get("displacement",{})
    if not event or disp.get("grade") not in {"MODERATE","STRONG","EXTREME"}: return []
    direction=event["direction"]
    for i in range(len(candles)-2,-1,-1):
        c=candles[i]; opposing=float(c["close"])<float(c["open"]) if direction=="LONG" else float(c["close"])>float(c["open"])
        if opposing:
            low,high=float(c["low"]),float(c["high"]); later=candles[i+1:]; touches=sum(low<=float(x["close"])<=high for x in later); invalidated=any(float(x["close"])<low for x in later) if direction=="LONG" else any(float(x["close"])>high for x in later)
            return [{"direction":direction,"low":low,"high":high,"state":"INVALIDATED" if invalidated else "MITIGATED" if touches else "FRESH","touches":touches,"mitigation":touches>0,"invalidation":low if direction=="LONG" else high,"flip":invalidated,"basis":"OHLCV_PROXY"}]
    return []
