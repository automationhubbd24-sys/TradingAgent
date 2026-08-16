"""Deterministic, paper-only market intelligence primitives for the web API."""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests
from sqlalchemy import create_engine, text

from tradingagents.dataflows.binance import normalize_binance_symbol

from .streaming import BinanceFuturesStream, MarketEvent, OrderBook

UTC = timezone.utc
FUTURES_URL = "https://fapi.binance.com"
TIMEFRAMES = ("4h", "1h", "30m", "15m", "5m")
TERMINAL_OUTCOMES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "INVALIDATED", "EXPIRED", "CANCELLED"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def database_url(url: str | None = None) -> str:
    value = url or os.getenv("DATABASE_URL") or os.getenv("TRADINGAGENTS_DATABASE_URL", "sqlite:///./.tradingagents/tradingagents.db")
    # SQLAlchemy uses postgresql+psycopg; accept the conventional deployment URL.
    return value.replace("postgres://", "postgresql+psycopg://", 1).replace("postgresql://", "postgresql+psycopg://", 1)


def database_path(url: str | None = None) -> str:
    value = database_url(url)
    if not value.startswith("sqlite:"):
        raise ValueError("database_path is only available for sqlite URLs")
    path = value[10:] if value.startswith("sqlite:///") else value[9:]
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return path


class _PostgresConnection:
    """Minimal DB-API-shaped adapter for the existing repository queries."""
    def __init__(self, engine: Any): self.engine, self.connection, self.context = engine, None, None
    def __enter__(self): self.context = self.engine.begin(); self.connection = self.context.__enter__(); return self
    def __exit__(self, *args: Any): return self.context.__exit__(*args)
    def execute(self, statement: str, parameters: Any = None) -> Any:
        if isinstance(parameters, dict):
            return self.connection.execute(text(statement), parameters)
        # Existing repository SQL uses qmark bindings; psycopg uses %s.
        return self.connection.exec_driver_sql(statement.replace("?", "%s"), parameters or ())


class Store:
    """Repository with SQLite development support and PostgreSQL-ready URL validation."""

    def __init__(self, url: str | None = None):
        self.url = database_url(url)
        self.is_sqlite = self.url.startswith("sqlite:")
        self.engine = create_engine(self.url, future=True) if not self.is_sqlite else None
        self.path = database_path(self.url) if self.is_sqlite else None
        self._lock = threading.RLock()
        self.initialize()

    def _connection(self) -> Any:
        if self.is_sqlite:
            connection = sqlite3.connect(self.path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            return connection
        return _PostgresConnection(self.engine)

    def initialize(self) -> None:
        tables = {
            "conversations": "id TEXT PRIMARY KEY, title TEXT NOT NULL, active_symbol TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL",
            "messages": "id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, FOREIGN KEY(conversation_id) REFERENCES conversations(id)",
            "trade_decisions": "id TEXT PRIMARY KEY, conversation_id TEXT REFERENCES conversations(id) ON DELETE SET NULL, symbol TEXT NOT NULL, direction TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL",
            "trade_snapshots": "id TEXT PRIMARY KEY, trade_id TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL",
            "trade_outcomes": "trade_id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL",
            "post_mortems": "trade_id TEXT PRIMARY KEY, payload_json TEXT NOT NULL, created_at TEXT NOT NULL",
            "lessons": "id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL",
            "patterns": "id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL",
            "candidate_rules": "id TEXT PRIMARY KEY, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL",
            "strategy_versions": "id TEXT PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL",
            "audit_records": "id TEXT PRIMARY KEY, event_type TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL",
            "market_snapshots": "id TEXT PRIMARY KEY, symbol TEXT NOT NULL, payload_json TEXT NOT NULL, created_at TEXT NOT NULL",
        }
        with self._lock, self._connection() as conn:
            for name, columns in tables.items():
                conn.execute(f"CREATE TABLE IF NOT EXISTS {name} ({columns})")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_decisions_created ON trade_decisions(created_at)")

    @staticmethod
    def _row(row: Any | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(getattr(row, "_mapping", row))
        for key in list(result):
            if key.endswith("_json"):
                result[key[:-5]] = json.loads(result.pop(key))
        return result

    def create_conversation(self, title: str = "New conversation", active_symbol: str | None = None) -> dict[str, Any]:
        record = {"id": uuid.uuid4().hex, "title": title.strip() or "New conversation", "active_symbol": active_symbol, "created_at": now_iso(), "updated_at": now_iso()}
        with self._lock, self._connection() as conn:
            conn.execute("INSERT INTO conversations VALUES (:id,:title,:active_symbol,:created_at,:updated_at)", record)
        return record

    def list_conversations(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM conversations ORDER BY updated_at DESC")]

    def conversation(self, conversation_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            return self._row(conn.execute("SELECT * FROM conversations WHERE id=?", (conversation_id,)).fetchone())

    def update_conversation(self, conversation_id: str, **changes: Any) -> dict[str, Any] | None:
        current = self.conversation(conversation_id)
        if not current:
            return None
        current.update({key: value for key, value in changes.items() if value is not None})
        current["updated_at"] = now_iso()
        with self._lock, self._connection() as conn:
            conn.execute("UPDATE conversations SET title=:title,active_symbol=:active_symbol,updated_at=:updated_at WHERE id=:id", current)
        return current

    def delete_conversation(self, conversation_id: str) -> bool:
        with self._lock, self._connection() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            conn.execute("UPDATE trade_decisions SET conversation_id=NULL WHERE conversation_id=?", (conversation_id,))
            return conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,)).rowcount > 0

    def add_message(self, conversation_id: str, role: str, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {"id": uuid.uuid4().hex, "conversation_id": conversation_id, "role": role, "content": content, "metadata_json": json.dumps(metadata or {}, separators=(",", ":")), "created_at": now_iso()}
        with self._lock, self._connection() as conn:
            conn.execute("INSERT INTO messages VALUES (:id,:conversation_id,:role,:content,:metadata_json,:created_at)", record)
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (record["created_at"], conversation_id))
        return {**record, "metadata": metadata or {}}

    def messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [self._row(row) for row in conn.execute("SELECT * FROM messages WHERE conversation_id=? ORDER BY created_at", (conversation_id,))]

    def save_snapshot(self, snapshot: dict[str, Any]) -> str:
        snapshot_id = uuid.uuid4().hex
        with self._lock, self._connection() as conn:
            conn.execute("INSERT INTO market_snapshots VALUES (?,?,?,?)", (snapshot_id, snapshot["symbol"], json.dumps(snapshot), now_iso()))
        return snapshot_id

    def save_trade_snapshot(self, trade_id: str, snapshot: dict[str, Any]) -> str:
        snapshot_id = uuid.uuid4().hex
        payload = {**snapshot, "trade_id": trade_id}
        with self._lock, self._connection() as conn:
            conn.execute("INSERT INTO trade_snapshots VALUES (?,?,?,?)", (snapshot_id, trade_id, json.dumps(payload), now_iso()))
        return snapshot_id

    def save_decision(self, decision: dict[str, Any], conversation_id: str | None = None) -> dict[str, Any]:
        decision.setdefault("id", uuid.uuid4().hex)
        decision.setdefault("created_at", now_iso())
        with self._lock, self._connection() as conn:
            conn.execute("INSERT INTO trade_decisions VALUES (?,?,?,?,?,?,?)", (decision["id"], conversation_id, decision["symbol"], decision["direction"], decision.get("entry_status", "NO_TRADE"), json.dumps(decision), decision["created_at"]))
        return decision

    def trade(self, trade_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload_json FROM trade_decisions WHERE id=?", (trade_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def trades(self) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute("SELECT payload_json FROM trade_decisions ORDER BY created_at DESC")]

    def outcome(self, trade_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload_json FROM trade_outcomes WHERE trade_id=?", (trade_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def upsert_outcome(self, trade_id: str, status: str, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        with self._lock, self._connection() as conn:
            existing = conn.execute("SELECT status,payload_json FROM trade_outcomes WHERE trade_id=?", (trade_id,)).fetchone()
            if existing and existing["status"] in TERMINAL_OUTCOMES:
                return json.loads(existing["payload_json"]), False
            timestamp = now_iso()
            payload = {**payload, "trade_id": trade_id, "status": status}
            if existing:
                conn.execute("UPDATE trade_outcomes SET status=?,payload_json=?,updated_at=? WHERE trade_id=?", (status, json.dumps(payload), timestamp, trade_id))
                return payload, False
            conn.execute("INSERT INTO trade_outcomes VALUES (?,?,?,?,?)", (trade_id, status, json.dumps(payload), timestamp, timestamp))
            return payload, True

    def postmortem(self, trade_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT payload_json FROM post_mortems WHERE trade_id=?", (trade_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_learning(self, table: str, payload: dict[str, Any], status: str = "OBSERVE") -> dict[str, Any]:
        record_id, timestamp = uuid.uuid4().hex, now_iso()
        payload = {**payload, "id": record_id, "status": status}
        with self._lock, self._connection() as conn:
            conn.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?)", (record_id, status, json.dumps(payload), timestamp, timestamp))
        return payload

    def list_learning(self, table: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            return [json.loads(row[0]) for row in conn.execute(f"SELECT payload_json FROM {table} ORDER BY created_at DESC")]


@dataclass
class StreamHealth:
    connected: bool = False
    last_event_at: float | None = None
    attempts: int = 0
    last_error: str | None = None


class BinanceWebSocketManager:
    """Dependency-free websocket lifecycle state usable by a future runner or tests."""
    def __init__(self, clock: Callable[[], float] = time.time, max_backoff: float = 30.0):
        self.clock, self.max_backoff, self.health = clock, max_backoff, StreamHealth()

    def on_connected(self) -> None:
        self.health.connected, self.health.attempts, self.health.last_error = True, 0, None
        self.health.last_event_at = self.clock()

    def on_message(self, message: Any) -> bool:
        if not isinstance(message, dict):
            self.health.last_error = "malformed websocket message"
            return False
        self.health.last_event_at = self.clock()
        return True

    def on_disconnected(self, error: Exception | str | None = None) -> float:
        self.health.connected = False
        self.health.attempts += 1
        self.health.last_error = str(error) if error else None
        return min(self.max_backoff, float(2 ** min(self.health.attempts - 1, 10)))

    def status(self, stale_after_seconds: float = 30.0) -> dict[str, Any]:
        age = None if self.health.last_event_at is None else self.clock() - self.health.last_event_at
        return {**asdict(self.health), "stale": age is None or age > stale_after_seconds, "age_seconds": age}


class BinanceMarketService:
    """Binance REST snapshotter with bounded retries and source-specific quality gates."""
    def __init__(self, base_url: str = FUTURES_URL, http_get: Callable[..., Any] = requests.get,
                 timeout: int = 10, clock: Callable[[], float] = time.time,
                 max_attempts: int | None = None, retry_backoff_seconds: float | None = None,
                 sleeper: Callable[[float], None] = time.sleep):
        self.base_url, self.http_get, self.timeout, self.clock = base_url.rstrip("/"), http_get, timeout, clock
        self.max_attempts = max(1, max_attempts if max_attempts is not None else int(os.getenv("TRADINGAGENTS_BINANCE_MAX_ATTEMPTS", "3")))
        self.retry_backoff_seconds = max(0.0, retry_backoff_seconds if retry_backoff_seconds is not None else float(os.getenv("TRADINGAGENTS_BINANCE_RETRY_BACKOFF_SECONDS", "0.25")))
        self.sleeper = sleeper

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        """Retry only transport faults and Binance overload/server responses."""
        last_error: Exception | None = None
        for attempt in range(self.max_attempts):
            try:
                response = self.http_get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
                status = getattr(response, "status_code", 200)
                if status < 400:
                    return response.json()
                error = RuntimeError(f"Binance HTTP {status}")
                if status not in (418, 429) and not 500 <= status < 600:
                    raise error
                last_error = error
            except (requests.RequestException, ConnectionError, TimeoutError) as exc:
                last_error = exc
            if attempt < self.max_attempts - 1:
                self.sleeper(min(5.0, self.retry_backoff_seconds * (2 ** attempt)))
        raise last_error or RuntimeError("Binance request failed")

    @staticmethod
    def _candles(rows: list[list[Any]]) -> list[dict[str, float | int]]:
        return [{"open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]), "close_time": int(r[6])} for r in rows if len(r) >= 7]

    @staticmethod
    def _timestamp(value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else fallback
        except (TypeError, ValueError):
            return fallback

    def snapshot(self, raw_symbol: str) -> dict[str, Any]:
        errors: dict[str, str] = {}
        try:
            symbol = normalize_binance_symbol(raw_symbol)
            symbol_error = None
        except Exception as exc:
            symbol, symbol_error = "", str(exc)
            errors["symbol"] = symbol_error
        now_ms = int(self.clock() * 1000)
        candle_thresholds = {interval: int(float(os.getenv(f"TRADINGAGENTS_CANDLE_MAX_AGE_{interval.upper()}_SECONDS", str(default)))) for interval, default in {"5m": 900, "15m": 1800, "30m": 3600, "1h": 7200, "4h": 28800}.items()}
        live_threshold = int(float(os.getenv("TRADINGAGENTS_LIVE_PRICE_MAX_AGE_SECONDS", "60")))
        candles: dict[str, list[dict[str, float | int]]] = {}
        fetched_at: dict[str, int] = {}
        def safe(key: str, path: str, params: dict[str, Any]) -> Any:
            try:
                value = self._get(path, params)
                fetched_at[key] = int(self.clock() * 1000)
                return value
            except Exception as exc:
                errors[key] = str(exc)
                return None
        for interval in TIMEFRAMES:
            rows = safe(f"candles.{interval}", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": 80}) if symbol else None
            # The current, still-forming candle is never historical input.
            candles[interval] = [candle for candle in self._candles(rows) if candle["close_time"] <= now_ms] if isinstance(rows, list) else []
        ticker = safe("ticker", "/fapi/v1/ticker/price", {"symbol": symbol}) if symbol else None
        mark = safe("mark_price", "/fapi/v1/premiumIndex", {"symbol": symbol}) if symbol else None
        depth = safe("order_book", "/fapi/v1/depth", {"symbol": symbol, "limit": 100}) if symbol else None
        trades = safe("trades", "/fapi/v1/aggTrades", {"symbol": symbol, "limit": 500}) if symbol else None
        oi = safe("open_interest", "/fapi/v1/openInterest", {"symbol": symbol}) if symbol else None
        ratio = safe("positioning", "/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "5m", "limit": 1}) if symbol else None
        price, mark_price, index_price = _number((ticker or {}).get("price")), _number((mark or {}).get("markPrice")), _number((mark or {}).get("indexPrice"))
        latest = {name: values[-1]["close_time"] for name, values in candles.items() if values}
        def detail(available: bool, timestamp: int | None, threshold: int, error: str | None = None) -> dict[str, Any]:
            age = None if timestamp is None else max(0.0, (now_ms - timestamp) / 1000)
            fresh = bool(available and timestamp is not None and age is not None and age <= threshold)
            return {"available": available, "fresh": fresh, "stale": bool(available and not fresh), "error": error, "timestamp": timestamp, "age_seconds": age, "threshold_seconds": threshold}
        sources: dict[str, dict[str, Any]] = {"symbol": detail(bool(symbol), now_ms if symbol else None, live_threshold, symbol_error)}
        sources["ticker"] = detail(price is not None, fetched_at.get("ticker"), live_threshold, errors.get("ticker"))
        mark_timestamp = self._timestamp((mark or {}).get("time"), fetched_at.get("mark_price", now_ms))
        sources["mark_price"] = detail(mark_price is not None, mark_timestamp if mark else None, live_threshold, errors.get("mark_price"))
        sources["index_price"] = detail(index_price is not None, mark_timestamp if mark else None, live_threshold, errors.get("mark_price"))
        for interval in TIMEFRAMES:
            sources[f"candles.{interval}"] = detail(len(candles[interval]) >= 20, latest.get(interval), candle_thresholds[interval], errors.get(f"candles.{interval}"))
        sources["order_book"] = detail(bool(depth and (depth.get("bids") or depth.get("asks"))), fetched_at.get("order_book"), live_threshold, errors.get("order_book"))
        sources["trades"] = detail(isinstance(trades, list) and bool(trades), fetched_at.get("trades"), live_threshold, errors.get("trades"))
        oi_timestamp = self._timestamp((oi or {}).get("time"), fetched_at.get("open_interest", now_ms))
        sources["open_interest"] = detail(_number((oi or {}).get("openInterest")) is not None, oi_timestamp if oi else None, live_threshold, errors.get("open_interest"))
        sources["positioning"] = detail(isinstance(ratio, list) and bool(ratio) and _number(ratio[-1].get("longShortRatio")) is not None, fetched_at.get("positioning"), live_threshold, errors.get("positioning"))
        sources["funding"] = detail(_number((mark or {}).get("lastFundingRate")) is not None, mark_timestamp if mark else None, live_threshold, errors.get("mark_price"))
        critical = ["symbol", "ticker", "mark_price", "index_price", *[f"candles.{interval}" for interval in TIMEFRAMES]]
        important, optional = ["order_book", "trades", "open_interest"], ["positioning", "funding"]
        def categorized(names: list[str], condition: str) -> list[str]: return [name for name in names if not sources[name][condition]]
        critical_missing, critical_stale = categorized(critical, "available"), categorized(critical, "fresh")
        critical_stale = [name for name in critical_stale if sources[name]["available"]]
        important_missing, important_stale = categorized(important, "available"), [name for name in important if sources[name]["stale"]]
        optional_missing, optional_stale = categorized(optional, "available"), [name for name in optional if sources[name]["stale"]]
        missing, stale = critical_missing + important_missing + optional_missing, critical_stale + important_stale + optional_stale
        bids = [(float(p), float(q)) for p, q in (depth or {}).get("bids", [])]
        asks = [(float(p), float(q)) for p, q in (depth or {}).get("asks", [])]
        bid_volume, ask_volume = sum(q for _, q in bids), sum(q for _, q in asks)
        buy_volume = sum(float(t.get("q", 0)) for t in (trades or []) if not t.get("m", False))
        sell_volume = sum(float(t.get("q", 0)) for t in (trades or []) if t.get("m", False))
        health = {"ticker": sources["ticker"], "mark_price": sources["mark_price"], "klines": {"status": "fresh" if all(sources[f"candles.{i}"]["fresh"] for i in TIMEFRAMES) else "degraded", "intervals": {i: sources[f"candles.{i}"] for i in TIMEFRAMES}}, "order_book": sources["order_book"], "trades": sources["trades"], "open_interest": sources["open_interest"], "positioning": sources["positioning"]}
        for value in health.values():
            if "status" not in value: value["status"] = "fresh" if value["fresh"] else ("stale" if value["stale"] else "unavailable")
        quality = {"valid": not critical_missing and not critical_stale, "fresh": not critical_stale, "critical_missing": critical_missing, "critical_stale": critical_stale, "important_missing": important_missing, "important_stale": important_stale, "optional_missing": optional_missing, "optional_stale": optional_stale, "missing": missing, "stale": stale, "errors": errors, "sources": sources, "health": health, "candle_thresholds_seconds": candle_thresholds, "live_price_threshold_seconds": live_threshold}
        return {"symbol": symbol, "timestamp": now_ms, "source": "binance_usdm_rest", "source_timestamps": {name: value["timestamp"] for name, value in sources.items()}, "price": price, "mark_price": mark_price, "index_price": index_price, "candles": candles, "order_book": {"bid_volume": bid_volume, "ask_volume": ask_volume, "imbalance": (bid_volume - ask_volume) / (bid_volume + ask_volume) if bid_volume + ask_volume else None}, "order_flow": {"taker_buy_volume": buy_volume, "taker_sell_volume": sell_volume, "imbalance": (buy_volume-sell_volume)/(buy_volume+sell_volume) if buy_volume + sell_volume else None}, "open_interest": {"value": _number((oi or {}).get("openInterest")), "timestamp": (oi or {}).get("time")}, "funding": {"rate": _number((mark or {}).get("lastFundingRate")), "next_funding_time": (mark or {}).get("nextFundingTime")}, "positioning": {"long_short_ratio": _number((ratio or [{}])[-1].get("longShortRatio"))}, "errors": errors, "quality": quality, "health": health, "btc_context": {}}

    def with_btc_context(self, raw_symbol: str) -> dict[str, Any]:
        snapshot = self.snapshot(raw_symbol)
        if snapshot["symbol"] != "BTCUSDT":
            try:
                btc = self.snapshot("BTCUSDT")
                snapshot["btc_context"] = {"symbol": btc["symbol"], "price": btc["price"], "structure": derive_structure(btc["candles"]), "quality": btc["quality"]}
            except Exception as exc:
                snapshot["btc_context"] = {"error": str(exc), "quality": {"valid": False, "fresh": False}}
        return snapshot


def _number(value: Any) -> float | None:
    try: return float(value)
    except (TypeError, ValueError): return None


def atr(candles: list[dict[str, Any]], period: int = 14) -> float | None:
    """Wilder-style true-range average over closed OHLCV candles."""
    if len(candles) < 2:
        return None
    ranges = []
    previous_close = float(candles[0]["close"])
    for candle in candles[1:]:
        high, low = float(candle["high"]), float(candle["low"])
        ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
        previous_close = float(candle["close"])
    values = ranges[-period:]
    return sum(values) / len(values) if values else None


def confirmed_swing_pivots(candles: list[dict[str, Any]], width: int = 2) -> dict[str, list[dict[str, Any]]]:
    """Return pivots only after `width` subsequent closed candles confirm them."""
    highs, lows = [], []
    for index in range(width, len(candles) - width):
        candle = candles[index]
        window = candles[index - width:index + width + 1]
        if float(candle["high"]) == max(float(item["high"]) for item in window): highs.append({"index": index, "price": float(candle["high"])})
        if float(candle["low"]) == min(float(item["low"]) for item in window): lows.append({"index": index, "price": float(candle["low"])})
    return {"highs": highs, "lows": lows}


def classify_swings(pivots: dict[str, list[dict[str, Any]]]) -> str:
    highs, lows = pivots["highs"], pivots["lows"]
    if len(highs) < 2 or len(lows) < 2: return "MIXED"
    high_state = "HH" if highs[-1]["price"] > highs[-2]["price"] else "LH"
    low_state = "HL" if lows[-1]["price"] > lows[-2]["price"] else "LL"
    return f"{high_state}_{low_state}" if (high_state, low_state) in {("HH", "HL"), ("LH", "LL")} else "MIXED"


def displacement(candles: list[dict[str, Any]], atr_value: float | None) -> dict[str, Any]:
    if not candles or not atr_value: return {"present": False, "direction": "NEUTRAL"}
    candle = candles[-1]
    body = abs(float(candle["close"]) - float(candle["open"]))
    average_volume = sum(float(item.get("volume", 0)) for item in candles[-20:]) / min(20, len(candles))
    direction = "LONG" if float(candle["close"]) > float(candle["open"]) else "SHORT"
    return {"present": body >= atr_value * 0.3 and float(candle.get("volume", 0)) >= average_volume, "direction": direction, "body": body, "volume": float(candle.get("volume", 0)), "average_volume": average_volume}


def closed_break(candles: list[dict[str, Any]], direction: str, atr_value: float | None) -> str | None:
    if len(candles) < 6 or not atr_value: return None
    close = float(candles[-1]["close"])
    reference = max(float(item["close"]) for item in candles[-6:-1]) if direction == "LONG" else min(float(item["close"]) for item in candles[-6:-1])
    crossed = close > reference + atr_value * 0.1 if direction == "LONG" else close < reference - atr_value * 0.1
    if not crossed: return None
    prior = float(candles[-2]["close"])
    reversal = prior <= reference if direction == "LONG" else prior >= reference
    return "CHOCH_LIKE" if reversal else "BOS"


def timeframe_assessment(candles: list[dict[str, Any]]) -> dict[str, Any]:
    if len(candles) < 6: return {"bias": "neutral", "structure": "INSUFFICIENT", "pivots": {"highs": [], "lows": []}, "atr": None, "bos": None, "displacement": {"present": False, "direction": "NEUTRAL"}}
    value = atr(candles)
    pivots = confirmed_swing_pivots(candles)
    structure = classify_swings(pivots)
    first, last = float(candles[-min(20, len(candles))]["close"]), float(candles[-1]["close"])
    bias = "bullish" if structure == "HH_HL" or (structure == "MIXED" and last > first) else "bearish" if structure == "LH_LL" or (structure == "MIXED" and last < first) else "neutral"
    direction = "LONG" if bias == "bullish" else "SHORT" if bias == "bearish" else "NEUTRAL"
    return {"bias": bias, "structure": structure, "pivots": pivots, "atr": value, "bos": closed_break(candles, direction, value) if direction != "NEUTRAL" else None, "displacement": displacement(candles, value), "last_close": last, "swing_high": max(float(item["high"]) for item in candles[-8:]), "swing_low": min(float(item["low"]) for item in candles[-8:])}


def derive_structure(candles: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    return {timeframe: timeframe_assessment(rows) for timeframe, rows in candles.items()}


def setup_zone(candles: list[dict[str, Any]], direction: str, assessment: dict[str, Any]) -> dict[str, float | str]:
    for candle in reversed(candles[:-1]):
        opposing = float(candle["close"]) < float(candle["open"]) if direction == "LONG" else float(candle["close"]) > float(candle["open"])
        if opposing:
            return {"low": float(candle["low"]), "high": float(candle["high"]), "source": "last_opposing_candle"}
    low, high = assessment["swing_low"], assessment["swing_high"]
    spread = high - low
    return {"low": low + spread * 0.382, "high": low + spread * 0.618, "source": "retracement_fallback"}


def _confidence(snapshot: dict[str, Any], aligned: int) -> float:
    modifier = 0.0
    for value in (snapshot.get("order_book", {}).get("imbalance"), snapshot.get("order_flow", {}).get("imbalance")):
        if isinstance(value, (int, float)): modifier += max(-0.025, min(0.025, value * 0.025))
    funding = snapshot.get("funding", {}).get("rate")
    ratio = snapshot.get("positioning", {}).get("long_short_ratio")
    if isinstance(funding, (int, float)): modifier -= max(-0.02, min(0.02, funding * 20))
    if isinstance(ratio, (int, float)): modifier += max(-0.02, min(0.02, (ratio - 1) * 0.02))
    return round(max(0.0, min(0.9, 0.45 + aligned * 0.1 + modifier)), 2)


def decide(snapshot: dict[str, Any]) -> dict[str, Any]:
    structure, quality = derive_structure(snapshot.get("candles", {})), snapshot.get("quality", {})
    base = {"id": uuid.uuid4().hex, "symbol": snapshot.get("symbol", "UNKNOWN"), "execution_mode": "PAPER_ONLY", "strategy_version": "decision-engine-2.0", "decision_state": "READY", "market_regime": "UNKNOWN", "structure": structure, "created_at": now_iso(), "limitations": ["OHLCV-led, closed-candle-only heuristic; not liquidation intelligence or full institutional SMC.", "Paper-only analysis; no external order execution."]}
    critical_missing, critical_stale = quality.get("critical_missing", []), quality.get("critical_stale", [])
    invalid = critical_missing + critical_stale or ((not quality.get("valid") or not quality.get("fresh")) and (quality.get("missing", []) + quality.get("stale", [])))
    if invalid or snapshot.get("price") is None:
        blockers = critical_missing + critical_stale or quality.get("missing", []) + quality.get("stale", [])
        return {**base, "decision_state": "DATA_UNAVAILABLE", "direction": "DATA_UNAVAILABLE", "directional_bias": "NEUTRAL", "entry_status": "UNAVAILABLE", "confidence": 0.0, "reason": "Analysis unavailable: critical live market data is missing or stale.", "conflicts": blockers, "data_diagnostic": {"critical_missing": critical_missing, "critical_stale": critical_stale, "blockers": blockers}}
    macro, confirmation = structure.get("4h", {}), structure.get("1h", {})
    if macro.get("bias") not in {"bullish", "bearish"} or macro.get("bias") != confirmation.get("bias"):
        return {**base, "direction": "NO_TRADE", "directional_bias": "NEUTRAL", "entry_status": "NO_TRADE", "confidence": _confidence(snapshot, 0), "reason": "4h macro bias and 1h confirmation are not aligned.", "conflicts": [f"4h={macro.get('bias')}", f"1h={confirmation.get('bias')}"]}
    direction = "LONG" if macro["bias"] == "bullish" else "SHORT"
    zone = setup_zone(snapshot["candles"]["30m"], direction, structure["30m"])
    base = {**base, "direction": direction, "directional_bias": direction, "setup_zone": zone, "confidence": _confidence(snapshot, 2), "market_regime": "TRENDING_UP" if direction == "LONG" else "TRENDING_DOWN"}
    fifteen, five = structure["15m"], structure["5m"]
    if fifteen.get("bias") != macro["bias"]:
        return {**base, "entry_status": "WAIT_FOR_PULLBACK", "reason": "Directional bias is intact; wait for a 15m pullback and stabilization in the 30m setup zone.", "conflicts": [f"15m={fifteen.get('bias')}"]}
    if five.get("bias") != macro["bias"] or not five.get("bos"):
        return {**base, "entry_status": "WAIT_FOR_5M_CONFIRMATION", "reason": "Directional bias is intact; wait for a closed 5m breakout/BOS confirmation.", "conflicts": [f"5m={five.get('bias')}", f"5m_break={five.get('bos')}"]}
    price = float(snapshot["price"])
    stop = fifteen["swing_low"] if direction == "LONG" else fifteen["swing_high"]
    risk = abs(price - stop)
    if risk <= 0 or risk / price > 0.1:
        return {**base, "direction": "NO_TRADE", "directional_bias": "NEUTRAL", "entry_status": "NO_TRADE", "reason": "Structure-derived stop is invalid.", "conflicts": []}
    targets = [price + risk * multiple if direction == "LONG" else price - risk * multiple for multiple in (2, 3)]
    return {**base, "entry_status": "CONFIRMED", "reason": "4h/1h structure, 30m zone, 15m stabilization, and a closed 5m breakout align; paper plan only.", "conflicts": [], "entry": price, "stop_loss": stop, "take_profits": targets, "invalidation": stop, "expected_rr": 2.0}


class OutcomeService:
    def __init__(self, store: Store): self.store = store

    def evaluate(self, trade_id: str, high: float, low: float, close: float, expires_at: str | None = None, source: str = "manual_ohlc", observed_at: str | None = None) -> tuple[dict[str, Any], bool]:
        trade = self.store.trade(trade_id)
        if not trade: raise KeyError("Trade not found")
        existing = self.store.outcome(trade_id)
        if existing and existing["status"] in TERMINAL_OUTCOMES:
            return existing, False
        evidence = {"high": high, "low": low, "close": close, "source": source, "observed_at": observed_at or now_iso(), "expires_at": expires_at}
        if trade["direction"] == "NO_TRADE":
            return self.store.upsert_outcome(trade_id, "CANCELLED", {**evidence, "reason": "No-trade decisions are not open paper positions."})
        long = trade["direction"] == "LONG"
        stop, target = float(trade["stop_loss"]), float(trade["take_profits"][0])
        target_hit = high >= target if long else low <= target
        stop_hit = low <= stop if long else high >= stop
        if target_hit and stop_hit:
            status = "UNRESOLVED"
            reason = "Single OHLC observation touched both TP and SL; intrabar order is unavailable."
        elif target_hit:
            status, reason = "TP1_HIT", None
        elif stop_hit:
            status, reason = "SL_HIT", None
        elif close <= trade.get("invalidation", stop) if long else close >= trade.get("invalidation", stop):
            status, reason = "INVALIDATED", None
        elif expires_at and now_iso() >= expires_at:
            status, reason = "EXPIRED", None
        else:
            status, reason = "OPEN", None
        if status == "OPEN":
            return {"trade_id": trade_id, "status": status, "evidence": evidence}, False
        outcome, created = self.store.upsert_outcome(trade_id, status, {**evidence, **({"reason": reason} if reason else {})})
        if created and status in TERMINAL_OUTCOMES:
            self._postmortem(trade, outcome)
            if trade.get("analysis_id"):
                from .hybrid import HybridAnalysisService
                HybridAnalysisService(self.store).record_learning(trade_id, outcome)
        return outcome, created

    def _postmortem(self, trade: dict[str, Any], outcome: dict[str, Any]) -> None:
        primary = "VALID_SETUP_NORMAL_LOSS" if outcome["status"] == "SL_HIT" else "UNKNOWN"
        payload = {"trade_id": trade["id"], "outcome": outcome["status"], "primary_error": primary, "summary": f"Paper trade closed as {outcome['status']}; strategy was not modified.", "created_at": now_iso()}
        with self.store._lock, self.store._connection() as conn:
            conn.execute("INSERT INTO post_mortems VALUES (?,?,?) ON CONFLICT(trade_id) DO NOTHING", (trade["id"], json.dumps(payload), now_iso()))
        lesson = self.store.save_learning("lessons", {"trade_id": trade["id"], "category": primary, "condition": trade["market_regime"], "lesson": "Observe repeated evidence before proposing a candidate rule.", "evidence_count": 1})
        self.store.save_learning("patterns", {"trade_id": trade["id"], "description": f"{trade['direction']} in {trade['market_regime']}", "outcome": outcome["status"], "lesson_id": lesson["id"]})


# Keep the established foundation import surface while delegating analysis to v3.
from .market_intelligence.decision import decide, derive_structure  # noqa: E402
