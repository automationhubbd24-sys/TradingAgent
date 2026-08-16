from __future__ import annotations
from typing import Any
from .structure import atr

def liquidity_map(candles: list[dict[str, Any]], state: dict[str, Any]) -> dict[str, Any]:
    tolerance=(atr(candles) or 0)*.15; highs=state.get("pivots",{}).get("highs",[]); lows=state.get("pivots",{}).get("lows",[])
    equal=[]
    for points, side in ((highs,"EQUAL_HIGHS"),(lows,"EQUAL_LOWS")):
        for a,b in zip(points,points[1:]):
            if abs(a["price"]-b["price"])<=tolerance: equal.append({"type":side,"price":round((a["price"]+b["price"])/2,8),"strength":round((a["strength"]+b["strength"])/2,3),"basis":"OHLCV_PROXY"})
    levels=[{"type":"SWING_HIGH","price":x["price"],"basis":"OHLCV_PROXY"} for x in highs[-3:]]+[{"type":"SWING_LOW","price":x["price"],"basis":"OHLCV_PROXY"} for x in lows[-3:]]
    sweep=None
    if len(candles)>=2:
        c,previous=candles[-1],candles[-2]
        for level in levels[-6:]:
            price=level["price"]
            if level["type"]=="SWING_HIGH" and float(c["high"])>price and float(c["close"])<price: sweep={"type":"SWEEP_REVERSAL","direction":"SHORT","level":price,"basis":"OHLCV_PROXY"}
            elif level["type"]=="SWING_LOW" and float(c["low"])<price and float(c["close"])>price: sweep={"type":"SWEEP_REVERSAL","direction":"LONG","level":price,"basis":"OHLCV_PROXY"}
            elif level["type"]=="SWING_HIGH" and float(c["close"])>price: sweep={"type":"SWEEP_CONTINUATION","direction":"LONG","level":price,"basis":"OHLCV_PROXY"}
            elif level["type"]=="SWING_LOW" and float(c["close"])<price: sweep={"type":"SWEEP_CONTINUATION","direction":"SHORT","level":price,"basis":"OHLCV_PROXY"}
    return {"equal_levels":equal,"levels":levels,"sweep":sweep,"basis":"OHLCV_PROXY"}

def sr_clusters(candles: list[dict[str, Any]], state: dict[str, Any]) -> list[dict[str, Any]]:
    tolerance=(atr(candles) or 0)*.25; levels=[p["price"] for group in state.get("pivots",{}).values() for p in group]
    clusters=[]
    for price in sorted(levels):
        found=next((x for x in clusters if abs(x["price"]-price)<=tolerance),None)
        if found: found["touches"]+=1; found["price"]=(found["price"]*(found["touches"]-1)+price)/found["touches"]
        else: clusters.append({"price":price,"touches":1,"basis":"OHLCV_PROXY"})
    return [x for x in clusters if x["touches"]>=2]
