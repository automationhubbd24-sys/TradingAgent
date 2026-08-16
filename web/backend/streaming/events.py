"""Normalized Binance USD-M Futures market events."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class MarketEvent:
    event_type: str
    symbol: str
    event_time: int
    received_at: float
    data: dict[str, Any]

    @classmethod
    def from_binance(cls, payload: Mapping[str, Any], received_at: float) -> "MarketEvent | None":
        event_type = payload.get("e")
        event_time = payload.get("E")
        symbol = payload.get("s")
        if event_type == "kline":
            kline = payload.get("k")
            if not isinstance(kline, Mapping):
                return None
            symbol = kline.get("s", symbol)
            data = {
                "interval": kline.get("i"), "open_time": kline.get("t"), "close_time": kline.get("T"),
                "open": kline.get("o"), "high": kline.get("h"), "low": kline.get("l"),
                "close": kline.get("c"), "volume": kline.get("v"), "closed": kline.get("x"),
            }
        elif event_type == "depthUpdate":
            data = {"first_update_id": payload.get("U"), "final_update_id": payload.get("u"), "previous_final_update_id": payload.get("pu"), "bids": payload.get("b", []), "asks": payload.get("a", [])}
        elif event_type == "bookTicker":
            data = {"bid_price": payload.get("b"), "bid_quantity": payload.get("B"), "ask_price": payload.get("a"), "ask_quantity": payload.get("A"), "update_id": payload.get("u")}
        elif event_type == "aggTrade":
            data = {"aggregate_trade_id": payload.get("a"), "price": payload.get("p"), "quantity": payload.get("q"), "first_trade_id": payload.get("f"), "last_trade_id": payload.get("l"), "trade_time": payload.get("T"), "buyer_is_maker": payload.get("m")}
        elif event_type == "forceOrder":
            order = payload.get("o")
            if not isinstance(order, Mapping):
                return None
            symbol = order.get("s", symbol)
            data = {"order_id": order.get("i"), "side": order.get("S"), "order_type": order.get("o"), "time_in_force": order.get("f"), "quantity": order.get("q"), "price": order.get("p"), "average_price": order.get("ap"), "order_status": order.get("X"), "filled_quantity": order.get("z"), "trade_time": order.get("T")}
        else:
            return None
        if not isinstance(symbol, str) or not symbol or not isinstance(event_time, int):
            return None
        return cls(event_type=event_type, symbol=symbol.upper(), event_time=event_time, received_at=received_at, data=data)
