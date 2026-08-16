from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Any
from .contracts import TIMEFRAMES, PROXY_LIMITATION, evidence
from .structure import assess
from .liquidity import liquidity_map, sr_clusters
from .zones import setup_zone
from .fvg import detect as fvg_detect
from .order_blocks import detect as ob_detect
from .breakouts import detect as breakout_detect
from .futures_context import build as futures_context
from .btc_context import modifier as btc_modifier
from .regime import classify
from .scoring import score
from .risk import plan

def derive_structure(candles: dict[str,list[dict[str,Any]]]) -> dict[str,Any]: return {tf:assess(candles.get(tf,[]),tf) for tf in TIMEFRAMES}

class DecisionEngineV3:
    def decide(self, snapshot: dict[str,Any]) -> dict[str,Any]:
        quality=snapshot.get("quality",{}); missing=quality.get("critical_missing",[])+quality.get("critical_stale",[])
        base={"id":uuid.uuid4().hex,"symbol":snapshot.get("symbol","UNKNOWN"),"execution_mode":"PAPER_ONLY","strategy_version":"decision-engine-3.0","created_at":datetime.now(timezone.utc).isoformat(),"limitations":[PROXY_LIMITATION,"Snapshot contains only up to 80 closed candles; no historical OI/funding trends, persistent order-book, or liquidation data.","Paper-only analysis; no execution."],"timeframe_weights":{"4h":.32,"1h":.27,"30m":.18,"15m":.14,"5m":.09}}
        if missing or not quality.get("valid", True) or not quality.get("fresh", True) or snapshot.get("price") is None:
            blockers=missing or quality.get("missing",[])+quality.get("stale",[])
            return {**base,"decision_state":"DATA_UNAVAILABLE","direction":"DATA_UNAVAILABLE","directional_bias":"NEUTRAL","entry_status":"UNAVAILABLE","entry_readiness":"UNAVAILABLE","tradeable":False,"confidence":0.0,"scores":{"bias":0,"setup":0,"entry":0,"risk":0,"overall":0},"reason":"Analysis unavailable: critical market data is missing or stale.","conflicts":blockers,"supporting_evidence":[],"contradicting_evidence":[],"data_diagnostic":{"blockers":blockers}}
        states=derive_structure(snapshot.get("candles",{})); macro,confirm=states["4h"],states["1h"]
        direction="LONG" if macro["bias"]==confirm["bias"]=="bullish" else "SHORT" if macro["bias"]==confirm["bias"]=="bearish" else None
        enriched={};
        for tf,state in states.items():
            candles=snapshot["candles"].get(tf,[]); liq=liquidity_map(candles,state); enriched[tf]={**state,"liquidity":liq,"support_resistance":sr_clusters(candles,state),"fvg":fvg_detect(candles),"order_blocks":ob_detect(candles,state),"breakout":breakout_detect(candles,state)}
        if not direction:
            return {**base,"decision_state":"NO_TRADE","direction":"NO_TRADE","directional_bias":"NEUTRAL","entry_status":"NO_TRADE","entry_readiness":"NOT_READY","tradeable":False,"confidence":0.0,"market_regime":classify(states),"structure":enriched,"scores":{"bias":0,"setup":0,"entry":0,"risk":0,"overall":0},"supporting_evidence":[],"contradicting_evidence":[evidence("hierarchy","NEUTRAL",f"4h={macro['bias']}; 1h={confirm['bias']}")],"conflicts":["4h and 1h directional hierarchy is not aligned."],"reason":"No trade: higher-timeframe direction is not aligned."}
        btc=btc_modifier(snapshot,direction); scores=score(states,direction,btc); price=float(snapshot["price"]); zone=setup_zone(snapshot["candles"]["30m"],direction,states["30m"])
        key="bullish" if direction=="LONG" else "bearish"; supports=[evidence("hierarchy",direction,"4h and 1h align",.8,"4h"),evidence("btc_context",direction,btc["reason"],abs(btc["score"])/10)]
        conflicts=[]
        if states["15m"]["bias"]!=key: conflicts.append("15m has not stabilized with higher-timeframe bias.")
        if states["5m"]["bias"]!=key or not (states["5m"].get("bos") or states["5m"].get("choch")): conflicts.append("5m lacks a closed, follow-through BOS/CHOCH confirmation.")
        extension=abs(price-states["15m"]["last_close"])/(states["15m"].get("atr") or price)*1
        if extension>1.5: conflicts.append("Price is extended versus 15m ATR; do not chase.")
        ready=not conflicts and scores["overall"]>=55; risk_plan=plan(price,direction,states["15m"],enriched["15m"]["liquidity"],enriched["15m"]["support_resistance"]) if ready else None
        if ready and not risk_plan:
            conflicts.append("No defendable structure-derived risk plan is available.")
        if ready and risk_plan: final=f"{direction}_READY"; status="CONFIRMED"; readiness="READY"; tradeable=True
        else: final=f"{direction}_WAIT"; status="WAIT_FOR_CONFIRMATION"; readiness="WAIT"; tradeable=False
        setups=[]
        for tf in ("30m","15m","5m"):
            if enriched[tf]["order_blocks"]: setups.append({"type":"ORDER_BLOCK","timeframe":tf,"state":enriched[tf]["order_blocks"][0]["state"],"evidence":"structural break plus displacement follow","basis":"OHLCV_PROXY"})
            if enriched[tf]["fvg"]: setups.append({"type":"FVG","timeframe":tf,"state":enriched[tf]["fvg"][-1]["state"],"basis":"OHLCV_PROXY"})
            if enriched[tf]["breakout"]["state"]!="NONE": setups.append({"type":enriched[tf]["breakout"]["state"],"timeframe":tf,"basis":"OHLCV_PROXY"})
        result={**base,"decision_state":final,"direction":direction,"directional_bias":direction,"entry_status":status,"entry_readiness":readiness,"tradeable":tradeable,"confidence":round(scores["overall"]/100,2),"scores":scores,"market_regime":classify(states),"structure":enriched,"futures_context":futures_context(snapshot),"btc_context":btc,"setup_zone":zone,"setups":setups,"supporting_evidence":supports,"contradicting_evidence":[evidence("conflict",direction,x,.7) for x in conflicts],"conflicts":conflicts,"extension_chase": {"detected":extension>1.5,"atr_multiple":round(extension,2)},"reason":"Hierarchy, setup, and closed 5m confirmation align; paper plan only." if tradeable else "Directional hierarchy is present, but entry conditions are not complete; wait without chasing."}
        if risk_plan: result.update(risk_plan); result["scores"]["risk"]=70
        return result

def decide(snapshot: dict[str,Any]) -> dict[str,Any]: return DecisionEngineV3().decide(snapshot)
