from __future__ import annotations
from typing import Any

def modifier(snapshot: dict[str, Any], direction: str) -> dict[str, Any]:
    btc=snapshot.get("btc_context") or {}; structure=btc.get("structure",{}); higher=[structure.get(x,{}).get("bias") for x in ("4h","1h")]
    if snapshot.get("symbol")=="BTCUSDT": return {"applied":False,"score":0,"reason":"BTC is the reference asset."}
    aligned=higher.count("bullish") if direction=="LONG" else higher.count("bearish"); opposing=higher.count("bearish") if direction=="LONG" else higher.count("bullish")
    return {"applied":bool(structure),"score":aligned*4-opposing*5,"reason":"BTC closed-OHLCV context modifies altcoin confidence; it is not a correlation forecast."}
