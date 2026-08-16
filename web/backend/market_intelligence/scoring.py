from __future__ import annotations
from typing import Any

def score(states: dict[str, dict[str, Any]], direction: str, btc: dict[str, Any]) -> dict[str, float]:
    weights={"4h":.32,"1h":.27,"30m":.18,"15m":.14,"5m":.09}; key="bullish" if direction=="LONG" else "bearish"
    bias=sum(weights[t]*100 for t in weights if states[t].get("bias")==key)
    setup=sum(12 for t in ("30m","15m") if states[t].get("displacement",{}).get("grade") in {"MODERATE","STRONG","EXTREME"}) + (20 if states["30m"].get("bias")==key else 0)
    entry=(35 if states["5m"].get("bias")==key else 0)+(25 if states["5m"].get("bos") else 0)+(10 if states["15m"].get("bias")==key else 0)
    overall=max(0,min(100,.45*bias+.25*setup+.3*entry+btc.get("score",0)))
    return {"bias":round(bias,1),"setup":round(min(100,setup*2),1),"entry":round(min(100,entry),1),"risk":0.0,"overall":round(overall,1)}
