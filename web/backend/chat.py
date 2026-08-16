from __future__ import annotations

import json
import re
from typing import Any, Iterator

from .foundation import BinanceMarketService, Store, decide

MARKET_WORDS = re.compile(r"\b(long|short|setup|analy[sz]e|analysis|market|price|trade|btc|eth)\b", re.I)
SYMBOL = re.compile(r"\b(?:[A-Z0-9]{2,20}(?:USDT|USDC)|BTC|ETH|SOL|XRP|BNB)\b", re.I)


def parse_intent(text: str, active_symbol: str | None = None) -> tuple[str, str | None]:
    match = SYMBOL.search(text.upper())
    symbol = match.group(0) if match else active_symbol
    return ("market_analysis" if MARKET_WORDS.search(text) else "general_chat"), symbol


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def stream_chat(store: Store, market: BinanceMarketService, conversation_id: str, text: str) -> Iterator[str]:
    conversation = store.conversation(conversation_id)
    if not conversation:
        raise KeyError("Conversation not found")
    intent, symbol = parse_intent(text, conversation.get("active_symbol"))
    store.add_message(conversation_id, "user", text, {"intent": intent})
    yield sse("message_start", {"conversation_id": conversation_id, "intent": intent})
    if intent == "general_chat":
        response = "I can provide paper-only Binance USD-M Futures market analysis when you ask about a symbol or setup."
        yield sse("partial_text", {"text": response})
        store.add_message(conversation_id, "assistant", response, {"intent": intent})
        yield sse("message_complete", {"conversation_id": conversation_id})
        return
    if not symbol:
        response = "Please provide a Binance USD-M Futures symbol, for example BTCUSDT."
        yield sse("partial_text", {"text": response})
        store.add_message(conversation_id, "assistant", response, {"intent": intent})
        yield sse("message_complete", {"conversation_id": conversation_id})
        return
    yield sse("status", {"stage": "fetching_market_data", "symbol": symbol})
    try:
        snapshot = market.with_btc_context(symbol)
        store.save_snapshot(snapshot)
        yield sse("market_data", {"symbol": snapshot["symbol"], "quality": snapshot["quality"], "price": snapshot["price"]})
        yield sse("analysis_stage", {"stage": "deterministic_structure_and_risk"})
        decision = decide(snapshot)
        decision = store.save_decision(decision, conversation_id)
        store.update_conversation(conversation_id, active_symbol=snapshot["symbol"])
        response = _response(decision)
        yield sse("partial_text", {"text": response})
        yield sse("analysis_result", {"decision": decision})
        store.add_message(conversation_id, "assistant", response, {"intent": intent, "trade_id": decision["id"]})
    except Exception as exc:
        response = "Analysis unavailable: live Binance market data could not be retrieved. No paper trade was created."
        yield sse("analysis_result", {"direction": "NO_TRADE", "reason": str(exc)})
        store.add_message(conversation_id, "assistant", response, {"intent": intent, "error": str(exc)})
    yield sse("message_complete", {"conversation_id": conversation_id})


def _response(decision: dict[str, Any]) -> str:
    if decision["direction"] == "NO_TRADE":
        return f"Decision: NO_TRADE. {decision['reason']}"
    return (f"Paper-only decision: {decision['direction']} {decision['symbol']}. "
            f"Entry {decision['entry']:.8g}, stop {decision['stop_loss']:.8g}, "
            f"TP1 {decision['take_profits'][0]:.8g}, expected RR {decision['expected_rr']:.1f}. "
            f"{decision['reason']}")
