from __future__ import annotations
from typing import Any

def detect(candles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    gaps=[]
    for i in range(2,len(candles)):
        a,c=candles[i-2],candles[i]; direction="LONG" if float(c["low"])>float(a["high"]) else "SHORT" if float(c["high"])<float(a["low"]) else None
        if direction:
            low,high=(float(a["high"]),float(c["low"])) if direction=="LONG" else (float(c["high"]),float(a["low"])); later=candles[i+1:]; filled=any(float(x["low"])<=low for x in later) if direction=="LONG" else any(float(x["high"])>=high for x in later)
            gaps.append({"direction":direction,"low":low,"high":high,"state":"FILLED" if filled else "OPEN","formed_index":i,"basis":"OHLCV_PROXY"})
    return gaps[-8:]
