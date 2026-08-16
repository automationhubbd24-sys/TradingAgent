from __future__ import annotations
from typing import Any
from .contracts import candle_time

def atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(candles) < 2: return None
    ranges=[]; previous=float(candles[0]["close"])
    for c in candles[1:]:
        high, low=float(c["high"]), float(c["low"]); ranges.append(max(high-low, abs(high-previous), abs(low-previous))); previous=float(c["close"])
    return sum(ranges[-period:]) / min(period, len(ranges)) if ranges else None

def pivots(candles: list[dict[str, Any]], timeframe: str, width: int = 2) -> dict[str, list[dict[str, Any]]]:
    result={"highs": [], "lows": []}
    for i in range(width, len(candles)-width):
        window=candles[i-width:i+width+1]; c=candles[i]; strength=round(min(1.0, (float(c["high"])-min(float(x["low"]) for x in window)) / max(atr(window) or 1, .0000001) / 3), 3)
        for side, field, compare in (("highs", "high", max), ("lows", "low", min)):
            price=float(c[field])
            if price == compare(float(x[field]) for x in window):
                result[side].append({"timestamp": candle_time(c), "timeframe": timeframe, "price": price, "strength": strength, "confirmed": True, "broken": False, "type": "SWING_HIGH" if side == "highs" else "SWING_LOW"})
    last=float(candles[-1]["close"]) if candles else 0
    for pivot in result["highs"]: pivot["broken"] = last > pivot["price"]
    for pivot in result["lows"]: pivot["broken"] = last < pivot["price"]
    return result

def relative_volume(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if not candles: return {"ratio": None, "label": "UNAVAILABLE"}
    values=[float(c.get("volume", 0)) for c in candles[-21:-1]]; avg=sum(values)/len(values) if values else 0; ratio=float(candles[-1].get("volume", 0))/avg if avg else None
    label="HIGH" if ratio and ratio >= 1.5 else "ABOVE_AVERAGE" if ratio and ratio >= 1.15 else "LOW" if ratio and ratio < .75 else "NORMAL"
    return {"ratio": round(ratio, 3) if ratio else None, "label": label}

def displacement(candles: list[dict[str, Any]], atr_value: float | None) -> dict[str, Any]:
    if not candles or not atr_value: return {"grade":"NONE", "direction":"NEUTRAL", "body_atr":None, "relative_volume": relative_volume(candles)}
    c=candles[-1]; body=abs(float(c["close"])-float(c["open"])); score=body/atr_value; rv=relative_volume(candles).get("ratio") or 0
    grade="EXTREME" if score>=1.5 and rv>=1.5 else "STRONG" if score>=.9 and rv>=1.15 else "MODERATE" if score>=.55 else "WEAK" if score>=.3 else "NONE"
    return {"grade":grade,"direction":"LONG" if float(c["close"])>float(c["open"]) else "SHORT","body_atr":round(score,3),"relative_volume":relative_volume(candles)}

def assess(candles: list[dict[str, Any]], timeframe: str) -> dict[str, Any]:
    if len(candles)<6: return {"timeframe":timeframe,"basis":"OHLCV_PROXY","bias":"neutral","structure":"INSUFFICIENT","pivots":{"highs":[],"lows":[]},"bos":None,"choch":None,"atr":None}
    value=atr(candles); ps=pivots(candles,timeframe); highs,lows=ps["highs"],ps["lows"]
    state="MIXED"; bias="neutral"
    if len(highs)>=2 and len(lows)>=2:
        hs="HH" if highs[-1]["price"]>highs[-2]["price"] else "LH" ; ls="HL" if lows[-1]["price"]>lows[-2]["price"] else "LL"; state=f"{hs}_{ls}" if (hs,ls) in (("HH","HL"),("LH","LL")) else "MIXED"; bias="bullish" if state=="HH_HL" else "bearish" if state=="LH_LL" else "neutral"
    if bias=="neutral": bias="bullish" if float(candles[-1]["close"])>float(candles[max(0,len(candles)-20)]["close"]) else "bearish"
    last=candles[-1]; close=float(last["close"]); prev=candles[-2]; follow= (close>float(prev["close"]) if bias=="bullish" else close<float(prev["close"]))
    relevant=highs[-1] if bias=="bullish" and highs else lows[-1] if lows else None
    # A monotonic run has no confirmed local pivot; use a strictly closed recent range only as a conservative proxy.
    level=relevant["price"] if relevant else (max(float(x["close"]) for x in candles[-6:-1]) if bias=="bullish" else min(float(x["close"]) for x in candles[-6:-1]))
    # The level is already a closed range boundary; require a strict close beyond it, not an additional ATR buffer.
    broken=close>level if bias=="bullish" else close<level
    event={"type":"BOS","direction":"LONG" if bias=="bullish" else "SHORT","level":level,"close_confirmed":True,"wick_confirmed":(float(last["high"])>level if bias=="bullish" else float(last["low"])<level),"follow_through":follow,"basis":"OHLCV_PROXY"} if broken and follow else None
    choch={**event,"type":"CHOCH"} if event and state=="MIXED" else None
    return {"timeframe":timeframe,"basis":"OHLCV_PROXY","bias":bias,"structure":state,"pivots":ps,"atr":value,"bos":event if not choch else None,"choch":choch,"displacement":displacement(candles,value),"relative_volume":relative_volume(candles),"last_close":close,"swing_high":max(float(c["high"]) for c in candles[-8:]),"swing_low":min(float(c["low"]) for c in candles[-8:])}
