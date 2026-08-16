from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .foundation import BinanceMarketService, Store
from .hybrid import HybridAnalysisService
from .synthesis import DecisionSynthesizer, configured_synthesizer

SYMBOL = re.compile(r"\b(?:[A-Z0-9]{2,20}(?:USDT|USDC)|BTC|ETH|SOL|XRP|BNB)\b", re.I)
INTENTS = ("buy_or_short", "long_setup", "short_setup", "why", "explain", "follow_up", "risk_question", "compare_assets", "history", "performance", "general")
MARKET_INTENTS = {"buy_or_short", "long_setup", "short_setup", "why", "explain", "follow_up", "risk_question", "compare_assets"}


def parse_intent(text: str, active_symbol: str | None = None) -> tuple[str, str | None]:
    """Classify without fetching market data; market work is intent-gated below."""
    normalized = text.lower().strip()
    symbols = SYMBOL.findall(text.upper())
    symbol = symbols[0] if symbols else active_symbol
    if len(set(symbols)) >= 2 or re.search(r"\b(compare|versus| vs\.?|difference between)\b", normalized):
        return "compare_assets", symbol
    if re.search(r"\b(history|previous|past (?:trades?|decisions?)|saved)\b", normalized): return "history", symbol
    if re.search(r"\b(performance|win rate|results?|pnl)\b", normalized): return "performance", symbol
    if re.search(r"\b(risk|stop|loss|position size|invalidation|rr)\b", normalized): return "risk_question", symbol
    if re.search(r"\b(why|reason|rationale)\b", normalized): return "why", symbol
    if re.search(r"\b(explain|what does|how does)\b", normalized): return "explain", symbol
    if re.search(r"\b(should i|buy or short)\b", normalized) and symbol: return "buy_or_short", symbol
    if re.search(r"\b(long|bullish|buy)\b", normalized): return "long_setup", symbol
    if re.search(r"\b(short|bearish|sell)\b", normalized): return "short_setup", symbol
    if re.search(r"\b(trade|setup|analy[sz]e|analysis|market|price)\b", normalized) and symbol: return "buy_or_short", symbol
    if active_symbol and re.search(r"\b(it|that|this|continue|more|then)\b", normalized): return "follow_up", symbol
    return "general", symbol


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def stream_chat(store: Store, market: BinanceMarketService, conversation_id: str, text: str, synthesizer: DecisionSynthesizer | None = None, hybrid_service: HybridAnalysisService | None = None) -> Iterator[str]:
    conversation = store.conversation(conversation_id)
    if not conversation: raise KeyError("Conversation not found")
    intent, symbol = parse_intent(text, conversation.get("active_symbol"))
    store.add_message(conversation_id, "user", text, {"intent": intent})
    yield sse("message_start", {"conversation_id": conversation_id, "intent": intent})
    if intent == "history":
        yield from _complete(store, conversation_id, intent, _history_response(store, symbol)); return
    if intent == "performance":
        yield from _complete(store, conversation_id, intent, _performance_response(store)); return
    if intent == "general":
        yield from _complete(store, conversation_id, intent, "I can help with paper-only Binance USD-M Futures analysis. Ask for a long or short setup and include a symbol such as BTCUSDT."); return
    if not symbol:
        yield from _complete(store, conversation_id, intent, "Please provide a Binance USD-M Futures symbol, for example BTCUSDT."); return
    service = hybrid_service or HybridAnalysisService(store)
    if intent in {"why", "explain", "follow_up"}:
        analysis = service.latest_reusable(conversation_id, symbol)
        if analysis:
            yield sse("analysis_state", {"analysis_id": analysis["id"], "fingerprint": analysis["fingerprint"], "cache": "reused", "expires_at": analysis.get("expires_at")})
            yield sse("llm_result", analysis["llm_result"])
            yield sse("validation_result", analysis["validation_result"])
            decision = {**analysis["decision"], "validation_result": analysis["validation_result"], "cache": "reused", "synthesis_mode": analysis["synthesis_mode"], "analysis_id": analysis["id"]}
            response = _reuse_response(intent, decision, analysis["verified_state"])
            yield sse("partial_text", {"text": response})
            yield sse("analysis_result", {"analysis_id": analysis["id"], "decision": decision, "analysis": analysis})
            store.add_message(conversation_id, "assistant", response, {"intent": intent, "analysis_id": analysis["id"], "analysis": analysis})
            yield sse("message_complete", {"conversation_id": conversation_id}); return
    yield sse("status", {"stage": "fetching_market_data", "symbol": symbol})
    try:
        snapshot = market.with_btc_context(symbol)
        store.save_snapshot(snapshot)
        quality = snapshot.get("quality", {})
        yield sse("market_data", {"symbol": snapshot["symbol"], "quality": quality, "health": snapshot.get("health", quality.get("health", {})), "price": snapshot.get("price"), "mark_price": snapshot.get("mark_price"), "index_price": snapshot.get("index_price")})
        yield sse("status", {"stage": "validating_market_data", "symbol": snapshot["symbol"]})
        for timeframe in ("4h", "1h", "30m", "15m", "5m"):
            yield sse("status", {"stage": f"loading_{timeframe}_structure", "symbol": snapshot["symbol"]})
        yield sse("status", {"stage": "analyzing_market", "symbol": snapshot["symbol"]})
        yield sse("status", {"stage": "assembling_verified_state", "symbol": snapshot["symbol"]})
        analysis = service.analyze(snapshot, conversation_id)
        yield sse("analysis_state", {"analysis_id": analysis["id"], "fingerprint": analysis["fingerprint"], "cache": analysis["cache"]})
        yield sse("status", {"stage": "reasoning_with_llm", "symbol": snapshot["symbol"]})
        yield sse("llm_result", analysis["llm_result"])
        yield sse("status", {"stage": "validating_decision", "symbol": snapshot["symbol"]})
        yield sse("validation_result", analysis["validation_result"])
        decision = {**analysis["decision"], "validation_result": analysis["validation_result"], "cache": analysis["cache"], "synthesis_mode": analysis["synthesis_mode"]}
        if decision["decision_state"] == "DATA_UNAVAILABLE":
            yield sse("validation_failure", {"stage": "validation_failure", "blockers": decision.get("conflicts", []), "reason": decision["reason"]})
        decision["analysis_id"] = analysis["id"]
        decision = store.save_decision(decision, conversation_id)
        store.update_conversation(conversation_id, active_symbol=snapshot["symbol"])
        response = _response(decision)
        yield sse("partial_text", {"text": response})
        yield sse("analysis_result", {"analysis_id": analysis["id"], "decision": decision})
        store.add_message(conversation_id, "assistant", response, {"intent": intent, "trade_id": decision["id"], "analysis_id": analysis["id"], "analysis": analysis})
    except Exception as exc:
        response = "Analysis unavailable: live Binance market data could not be retrieved. No paper trade was created."
        decision = {"direction": "DATA_UNAVAILABLE", "entry_status": "UNAVAILABLE", "confidence": 0.0, "reason": str(exc)}
        yield sse("validation_failure", {"stage": "validation_failure", "blockers": ["market_data"], "reason": str(exc)})
        yield sse("analysis_result", {"decision": decision})
        store.add_message(conversation_id, "assistant", response, {"intent": intent, "error": str(exc)})
    yield sse("message_complete", {"conversation_id": conversation_id})


def _complete(store: Store, conversation_id: str, intent: str, response: str) -> Iterator[str]:
    yield sse("partial_text", {"text": response})
    store.add_message(conversation_id, "assistant", response, {"intent": intent})
    yield sse("message_complete", {"conversation_id": conversation_id})


def _reuse_response(intent: str, decision: dict[str, Any], state: dict[str, Any]) -> str:
    evidence = state.get("evidence", [])[:3]
    support = "; ".join(str(item.get("detail", "validated evidence")) for item in evidence) or "No additional evidence was stored."
    return f"Reused the latest validated analysis for {decision.get('symbol', 'this market')} without a new market request. {intent.replace('_', ' ').capitalize()}: {decision.get('reason', 'See the saved decision.')}. Evidence: {support}"


def _history_response(store: Store, symbol: str | None) -> str:
    decisions = [item for item in store.trades() if not symbol or item.get("symbol") == symbol]
    if not decisions: return "There are no saved paper decisions for that scope yet."
    latest = decisions[0]
    return f"Saved paper decisions: {len(decisions)}. Latest: {latest['direction']} {latest['symbol']} ({latest.get('entry_status', 'NO_TRADE')})."


def _performance_response(store: Store) -> str:
    decisions = store.trades()
    actionable = [item for item in decisions if item.get("direction") not in {"NO_TRADE", "DATA_UNAVAILABLE"}]
    terminal = sum(bool(store.outcome(item["id"])) for item in actionable)
    return f"Paper-only performance: {len(decisions)} decisions, {len(actionable)} actionable positions, {terminal} evaluated outcomes."


def _response(decision: dict[str, Any]) -> str:
    if decision["decision_state"] == "DATA_UNAVAILABLE": return f"Decision: DATA_UNAVAILABLE. {decision['reason']}"
    if decision["decision_state"] == "NO_TRADE": return f"Decision: NO_TRADE. {decision['reason']}"
    if decision["entry_status"] != "CONFIRMED":
        zone = decision.get("setup_zone", {})
        return f"Decision: {decision['decision_state']}. Directional bias: {decision['directional_bias']}. Readiness: {decision['entry_status']}. Zone {zone.get('low', 0):.8g}-{zone.get('high', 0):.8g}. {decision['reason']}"
    return (f"Paper-only confirmed plan ({decision['decision_state']}): {decision['directional_bias']} {decision['symbol']}. "
            f"Entry {decision['entry']:.8g}, stop {decision['stop_loss']:.8g}, "
            f"targets {decision['take_profits'][0]:.8g} (2R) / {decision['take_profits'][1]:.8g} (3R). "
            f"{decision['reason']}")
