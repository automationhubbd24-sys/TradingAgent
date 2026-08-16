"""Binance USD-M Futures combined-stream client and in-memory market state."""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Any, Callable, Iterable

import websocket

from .book import OrderBook
from .events import MarketEvent

# Binance USD-M public market streams use the routed endpoint; legacy unrouted URLs retire in 2026.
FUTURES_WS_URL = "wss://fstream.binance.com/public/stream?streams="
INTERVALS = ("5m", "15m", "30m", "1h", "4h")


class BinanceFuturesStream:
    def __init__(self, symbols: Iterable[str] = (), snapshot_fetcher: Callable[[str], dict[str, Any]] | None = None,
                 websocket_factory: Callable[..., Any] = websocket.create_connection, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep, max_backoff: float = 30.0) -> None:
        self.symbols = tuple(sorted({symbol.upper() for symbol in symbols}))
        self.snapshot_fetcher, self.websocket_factory, self.clock, self.sleeper = snapshot_fetcher, websocket_factory, clock, sleeper
        self.max_backoff, self._stop, self._thread, self._lock = max_backoff, threading.Event(), None, threading.RLock()
        self.connected, self.attempts, self.last_event_at, self.last_error, self.connected_at = False, 0, None, None, None
        self.events: deque[MarketEvent] = deque(maxlen=2_000)
        self.klines: dict[str, dict[str, dict[str, Any]]] = {}
        self.book_tickers: dict[str, dict[str, Any]] = {}
        self.agg_trades: dict[str, deque[dict[str, Any]]] = {}
        self.force_orders: dict[str, deque[dict[str, Any]]] = {}
        self.books: dict[str, OrderBook] = {}

    @property
    def url(self) -> str:
        streams = []
        for symbol in self.symbols:
            name = symbol.lower()
            streams.extend([*(f"{name}@kline_{interval}" for interval in INTERVALS), f"{name}@depth@100ms", f"{name}@bookTicker", f"{name}@aggTrade", f"{name}@forceOrder"])
        return FUTURES_WS_URL + "/".join(streams)

    def start(self) -> None:
        if not self.symbols or self._thread and self._thread.is_alive(): return
        self._stop.clear(); self._thread = threading.Thread(target=self._run, name="binance-usdm-stream", daemon=True); self._thread.start()

    def shutdown(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout)
        with self._lock: self.connected = False

    def _run(self) -> None:
        while not self._stop.is_set():
            connection = None
            try:
                connection = self.websocket_factory(self.url, timeout=10)
                with self._lock: self.connected, self.attempts, self.last_error, self.connected_at = True, 0, None, self.clock()
                while not self._stop.is_set():
                    if self.connected_at is not None and self.clock() - self.connected_at >= 23 * 60 * 60 + 55 * 60:
                        raise ConnectionError("controlled reconnect before Binance 24-hour websocket limit")
                    raw = connection.recv()
                    if not raw: raise ConnectionError("websocket closed")
                    self.handle_message(json.loads(raw))
            except Exception as exc:
                with self._lock:
                    self.connected, self.attempts, self.last_error = False, self.attempts + 1, str(exc)
                    delay = min(self.max_backoff, float(2 ** min(self.attempts - 1, 10)))
                if not self._stop.wait(delay): continue
            finally:
                if connection:
                    try: connection.close()
                    except Exception: pass

    def handle_message(self, message: Any) -> bool:
        payload = message.get("data", message) if isinstance(message, dict) else None
        event = MarketEvent.from_binance(payload, self.clock()) if isinstance(payload, dict) else None
        if not event:
            with self._lock: self.last_error = "malformed or unsupported websocket message"
            return False
        with self._lock:
            self.last_event_at = event.received_at; self.events.append(event)
            symbol = event.symbol
            if event.event_type == "kline": self.klines.setdefault(symbol, {})[event.data["interval"]] = event.data
            elif event.event_type == "bookTicker": self.book_tickers[symbol] = event.data
            elif event.event_type == "aggTrade": self.agg_trades.setdefault(symbol, deque(maxlen=500)).append(event.data)
            elif event.event_type == "forceOrder": self.force_orders.setdefault(symbol, deque(maxlen=500)).append(event.data)
            elif event.event_type == "depthUpdate": self._apply_depth(symbol, payload)
        return True

    def _apply_depth(self, symbol: str, diff: dict[str, Any]) -> None:
        book = self.books.setdefault(symbol, OrderBook())
        if book.resync_required and self.snapshot_fetcher:
            try: book.apply_snapshot(self.snapshot_fetcher(symbol))
            except Exception as exc: self.last_error = f"order book snapshot failed: {exc}"; return
        book.apply_diff(diff)

    def status(self, stale_after_seconds: float = 30.0) -> dict[str, Any]:
        with self._lock:
            age = None if self.last_event_at is None else self.clock() - self.last_event_at
            return {"connected": self.connected, "attempts": self.attempts, "last_event_at": self.last_event_at, "last_error": self.last_error, "stale": age is None or age > stale_after_seconds, "age_seconds": age, "symbols": list(self.symbols), "shutdown": self._stop.is_set(), "reconnect_before_seconds": 23 * 60 * 60 + 55 * 60}

    def state(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            normalized = symbol.upper(); book = self.books.get(normalized)
            return {"symbol": normalized, "klines": self.klines.get(normalized, {}).copy(), "book_ticker": self.book_tickers.get(normalized), "order_book": book.summary() if book else {"resync_required": True}, "agg_trades": list(self.agg_trades.get(normalized, ())), "force_orders": list(self.force_orders.get(normalized, ())), "stream": self.status()}
