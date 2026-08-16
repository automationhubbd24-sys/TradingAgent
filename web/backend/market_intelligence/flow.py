"""Deterministic liquidity and derivatives-flow observations for plain snapshots."""
from __future__ import annotations

from typing import Any


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _value(record: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return None


def taker_flow(trades: list[dict[str, Any]]) -> dict[str, Any]:
    buy = sell = 0.0
    for trade in trades:
        quantity = _number(_value(trade, "quantity", "qty", "q", "size")) or 0.0
        side = str(_value(trade, "side", "taker_side") or "").upper()
        buyer_maker = trade.get("is_buyer_maker", trade.get("m"))
        if side == "BUY" or buyer_maker is False:
            buy += quantity
        elif side == "SELL" or buyer_maker is True:
            sell += quantity
    total = buy + sell
    imbalance = (buy - sell) / total if total else None
    return {
        "basis": "AGG_TRADES",
        "status": "AVAILABLE" if total else "UNAVAILABLE",
        "trade_count": len(trades),
        "buy_volume": round(buy, 8),
        "sell_volume": round(sell, 8),
        "imbalance": round(imbalance, 4) if imbalance is not None else None,
        "state": "BUYER_AGGRESSIVE" if imbalance is not None and imbalance >= 0.2 else "SELLER_AGGRESSIVE" if imbalance is not None and imbalance <= -0.2 else "BALANCED" if imbalance is not None else "UNAVAILABLE",
    }


def _contextual_state(current: Any, history: Any, positive: str, negative: str) -> dict[str, Any]:
    value = _number(current)
    values = [_number(item.get("value", item) if isinstance(item, dict) else item) for item in history or []]
    values = [item for item in values if item is not None]
    if value is None:
        return {"value": None, "status": "UNAVAILABLE", "state": "UNAVAILABLE"}
    change = value - values[-1] if values else None
    return {
        "value": value,
        "status": "CONTEXTUAL" if change is not None else "CURRENT_ONLY",
        "change": round(change, 8) if change is not None else None,
        "state": positive if change is not None and change > 0 else negative if change is not None and change < 0 else "STABLE" if change is not None else "CURRENT",
    }


def order_book_evidence(book: dict[str, Any]) -> dict[str, Any]:
    bids = book.get("bids", [])
    asks = book.get("asks", [])
    bid_size = sum((_number(row[1] if isinstance(row, (list, tuple)) and len(row) > 1 else row.get("quantity", row.get("qty"))) or 0.0) for row in bids)
    ask_size = sum((_number(row[1] if isinstance(row, (list, tuple)) and len(row) > 1 else row.get("quantity", row.get("qty"))) or 0.0) for row in asks)
    total = bid_size + ask_size
    imbalance = (bid_size - ask_size) / total if total else _number(book.get("imbalance"))
    return {
        "basis": "ORDER_BOOK_SNAPSHOT",
        "status": "AVAILABLE" if imbalance is not None else "UNAVAILABLE",
        "bid_size": round(bid_size, 8),
        "ask_size": round(ask_size, 8),
        "imbalance": round(imbalance, 4) if imbalance is not None else None,
        "state": "BID_HEAVY" if imbalance is not None and imbalance >= 0.2 else "ASK_HEAVY" if imbalance is not None and imbalance <= -0.2 else "BALANCED" if imbalance is not None else "UNAVAILABLE",
    }


def liquidations_evidence(liquidations: list[dict[str, Any]]) -> dict[str, Any]:
    long = short = 0.0
    for item in liquidations:
        amount = _number(_value(item, "quantity", "qty", "size", "notional")) or 0.0
        side = str(item.get("side", "")).upper()
        if side == "BUY":
            short += amount
        elif side == "SELL":
            long += amount
    total = long + short
    return {
        "basis": "LIQUIDATION_FEED",
        "status": "AVAILABLE" if total else "UNAVAILABLE",
        "long_liquidated": round(long, 8),
        "short_liquidated": round(short, 8),
        "state": "LONG_LIQUIDATION_PRESSURE" if long > short else "SHORT_LIQUIDATION_PRESSURE" if short > long else "BALANCED" if total else "UNAVAILABLE",
    }


def analyze(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Derive compact flow evidence; absent feeds remain explicitly unavailable."""
    derivatives = snapshot.get("derivatives", {})
    oi = snapshot.get("open_interest", derivatives.get("open_interest", {}))
    funding = snapshot.get("funding", derivatives.get("funding", {}))
    oi_history = oi.get("history", derivatives.get("open_interest_history", [])) if isinstance(oi, dict) else []
    funding_history = funding.get("history", derivatives.get("funding_history", [])) if isinstance(funding, dict) else []
    oi_value = oi.get("value") if isinstance(oi, dict) else oi
    funding_value = funding.get("rate") if isinstance(funding, dict) else funding
    funding_state = _contextual_state(funding_value, funding_history, "FUNDING_RISING", "FUNDING_FALLING")
    if funding_state["value"] is not None and funding_state["status"] == "CURRENT_ONLY":
        funding_state["state"] = "POSITIVE" if funding_state["value"] > 0 else "NEGATIVE" if funding_state["value"] < 0 else "NEUTRAL"
    return {
        "taker_flow": taker_flow(snapshot.get("agg_trades", snapshot.get("trades", []))),
        "open_interest": _contextual_state(oi_value, oi_history, "OI_RISING", "OI_FALLING"),
        "funding": funding_state,
        "liquidations": liquidations_evidence(snapshot.get("liquidations", [])),
        "order_book": order_book_evidence(snapshot.get("order_book", {})),
    }
