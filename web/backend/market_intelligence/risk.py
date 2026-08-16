from __future__ import annotations
from typing import Any

def plan(price: float, direction: str, state: dict[str, Any], liquidity: dict[str, Any], sr: list[dict[str, Any]]) -> dict[str, Any] | None:
    stop=state.get("swing_low") if direction=="LONG" else state.get("swing_high"); risk=abs(price-float(stop))
    if not risk or risk/price>.1: return None
    candidates=[x["price"] for x in liquidity.get("levels",[])+sr if (x["price"]>price if direction=="LONG" else x["price"]<price)]
    candidates=sorted(candidates, reverse=direction=="SHORT"); targets=candidates[:2]
    while len(targets)<2: targets.append(price+risk*(2+len(targets)) if direction=="LONG" else price-risk*(2+len(targets)))
    return {"entry":price,"stop_loss":stop,"take_profits":targets,"invalidation":stop,"expected_rr":round(abs(targets[0]-price)/risk,2),"target_basis":"opposing_liquidity_or_sr_then_risk_multiple_fallback"}
