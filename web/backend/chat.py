from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .foundation import BinanceMarketService, Store, decide
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
    if re.search(r"\b(history|previous|past (?:trades?|decisions?)|saved)\b", normalized):
        return "history", symbol
    if re.search(r"\b(performance|win rate|results?|pnl)\b", normalized):
        return "performance", symbol
    if re.search(r"\b(risk|stop|loss|position size|invalidation|rr)\b", normalized):
        return "risk_question", symbol
    if re.search(r"\b(why|reason|rationale)\b", normalized):
        return "why", symbol
    if re.search(r"\b(explain|what does|how does)\b", normalized):
        return "explain", symbol
    if re.search(r"\b(should i|buy or short)\b", normalized) and symbol:
        return "buy_or_short", symbol
    if re.search(r"\b(long|bullish|buy)\b", normalized):
        return "long_setup", symbol
    if re.search(r"\b(short|bearish|sell)\b", normalized):
        return "short_setup", symbol
    if re.search(r"\b(trade|setup|analy[sz]e|analysis|market|price)\b", normalized) and symbol:
        return "buy_or_short", symbol
    if active_symbol and re.search(r"\b(it|that|this|continue|more|then)\b", normalized):
        return "follow_up", symbol
    return "general", symbol


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def stream_chat(store: Store, market: BinanceMarketService, conversation_id: str, text: str, synthesizer: DecisionSynthesizer | None = None) -> Iterator[str]:
    conversation = store.conversation(conversation_id)
    if not conversation:
        raise KeyError("Conversation not found")
    intent, symbol = parse_intent(text, conversation.get("active_symbol"))
    store.add_message(conversation_id, "user", text, {"intent": intent})
    yield sse("message_start", {"conversation_id": conversation_id, "intent": intent})

    if intent == "history":
        response = _history_response(store, symbol)
        yield from _complete(store, conversation_id, intent, response)
        return
    if intent == "performance":
        response = _performance_response(store)
        yield from _complete(store, conversation_id, intent, response)
        return
    if intent == "general":
        response = "I can help with paper-only Binance USD-M Futures analysis. Ask for a long or short setup and include a symbol such as BTCUSDT."
        yield from _complete(store, conversation_id, intent, response)
        return
    if not symbol:
        response = "Please provide a Binance USD-M Futures symbol, for example BTCUSDT."
        yield from _complete(store, conversation_id, intent, response)
        return

    yield sse("status", {"stage": "fetching_market_data", "symbol": symbol})
    try:
        snapshot = market.with_btc_context(symbol)
        store.save_snapshot(snapshot)
        yield sse("market_data", {"symbol": snapshot["symbol"], "quality": snapshot["quality"], "price": snapshot["price"]})
        yield sse("analysis_stage", {"stage": "deterministic_structure_and_risk"})
        decision = store.save_decision(decide(snapshot), conversation_id)
        store.update_conversation(conversation_id, active_symbol=snapshot["symbol"])
        response = _response(decision)
        # Synthesis is supplementary, bounded, and only eligible after a usable snapshot.
        if snapshot.get("quality", {}).get("valid") and snapshot.get("quality", {}).get("fresh"):
            summary = (synthesizer or configured_synthesizer()).summarize(snapshot, decision)
            if summary:
                response = f"{response}\n\nAdditional context: {summary}"
        yield sse("partial_text", {"text": response})
        yield sse("analysis_result", {"decision": decision})
        store.add_message(conversation_id, "assistant", response, {"intent": intent, "trade_id": decision["id"]})
    except Exception as exc:
        response = "Analysis unavailable: live Binance market data could not be retrieved. No paper trade was created."
        yield sse("analysis_result", {"direction": "NO_TRADE", "reason": str(exc)})
        store.add_message(conversation_id, "assistant", response, {"intent": intent, "error": str(exc)})
    yield sse("message_complete", {"conversation_id": conversation_id})


def _complete(store: Store, conversation_id: str, intent: str, response: str) -> Iterator[str]:
    yield sse("partial_text", {"text": response})
    store.add_message(conversation_id, "assistant", response, {"intent": intent})
    yield sse("message_complete", {"conversation_id": conversation_id})


def _history_response(store: Store, symbol: str | None) -> str:
    decisions = [item for item in store.trades() if not symbol or item.get("symbol") == symbol]
    if not decisions:
        return "There are no saved paper decisions for that scope yet."
    latest = decisions[0]
    return f"Saved paper decisions: {len(decisions)}. Latest: {latest['direction']} {latest['symbol']} ({latest.get('entry_status', 'NO_TRADE')})."


def _performance_response(store: Store) -> str:
    decisions = store.trades()
    actionable = [item for item in decisions if item.get("direction") != "NO_TRADE"]
    terminal = sum(bool(store.outcome(item["id"])) for item in actionable)
    return f"Paper-only performance: {len(decisions)} decisions, {len(actionable)} actionable positions, {terminal} evaluated outcomes."


def _response(decision: dict[str, Any]) -> str:
    if decision["direction"] == "NO_TRADE":
        return f"Decision: NO_TRADE. {decision['reason']}"
    return (f"Paper-only decision: {decision['direction']} {decision['symbol']}. "
            f"Entry {decision['entry']:.8g}, stop {decision['stop_loss']:.8g}, "
            f"TP1 {decision['take_profits'][0]:.8g}, expected RR {decision['expected_rr']:.1f}. "
            f"{decision['reason']}")
