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

from tradingagents.dataflows.binance import normalize_binance_symbol

UTC = timezone.utc
FUTURES_URL = "https://fapi.binance.com"
TIMEFRAMES = ("4h", "1h", "30m", "15m", "5m")
TERMINAL_OUTCOMES = {"TP1_HIT", "TP2_HIT", "TP3_HIT", "SL_HIT", "INVALIDATED", "EXPIRED", "CANCELLED"}


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def database_path(url: str | None = None) -> str:
    value = url or os.getenv("TRADINGAGENTS_DATABASE_URL", "sqlite:///./.tradingagents/tradingagents.db")
    if value.startswith("sqlite:///"):
        path = value[10:]
    elif value.startswith("sqlite://"):
        path = value[9:]
    elif "://" in value:
        raise ValueError("Only sqlite database URLs are supported by this backend foundation")
    else:
        path = value
    if path != ":memory:":
        Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    return path


class Store:
    """Small sqlite repository; JSON payloads preserve evolving market evidence."""

    def __init__(self, url: str | None = None):
        self.path = database_path(url)
        self._lock = threading.RLock()
        self.initialize()

    def _connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

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
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
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
    def __init__(self, base_url: str = FUTURES_URL, http_get: Callable[..., Any] = requests.get, timeout: int = 10, clock: Callable[[], float] = time.time):
        self.base_url, self.http_get, self.timeout, self.clock = base_url.rstrip("/"), http_get, timeout, clock

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        response = self.http_get(f"{self.base_url}{path}", params=params, timeout=self.timeout)
        if getattr(response, "status_code", 200) >= 400:
            raise RuntimeError(f"Binance HTTP {response.status_code}")
        return response.json()

    @staticmethod
    def _candles(rows: list[list[Any]]) -> list[dict[str, float | int]]:
        return [{"open_time": int(r[0]), "open": float(r[1]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4]), "volume": float(r[5]), "close_time": int(r[6])} for r in rows if len(r) >= 7]

    def snapshot(self, raw_symbol: str) -> dict[str, Any]:
        symbol, errors, candles = normalize_binance_symbol(raw_symbol), {}, {}
        now_ms = int(self.clock() * 1000)
        max_age_ms = int(float(os.getenv("TRADINGAGENTS_MARKET_MAX_AGE_SECONDS", "900")) * 1000)
        def safe(key: str, path: str, params: dict[str, Any]) -> Any:
            try:
                return self._get(path, params)
            except Exception as exc:
                errors[key] = str(exc)
                return None
        for interval in TIMEFRAMES:
            rows = safe(f"candles.{interval}", "/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": 80})
            candles[interval] = [candle for candle in self._candles(rows) if candle["close_time"] <= now_ms] if isinstance(rows, list) else []
        ticker = safe("price", "/fapi/v1/ticker/price", {"symbol": symbol})
        mark = safe("mark", "/fapi/v1/premiumIndex", {"symbol": symbol})
        depth = safe("depth", "/fapi/v1/depth", {"symbol": symbol, "limit": 100})
        trades = safe("aggregate_trades", "/fapi/v1/aggTrades", {"symbol": symbol, "limit": 500})
        oi = safe("open_interest", "/fapi/v1/openInterest", {"symbol": symbol})
        ratio = safe("long_short_ratio", "/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "5m", "limit": 1})
        missing = [f"candles.{name}" for name, value in candles.items() if len(value) < 20]
        latest_candle_times = {name: values[-1]["close_time"] for name, values in candles.items() if values}
        stale = [f"candles.{name}" for name, timestamp in latest_candle_times.items() if now_ms - timestamp > max_age_ms]
        if not ticker or not mark: missing.append("price")
        if not oi: missing.append("open_interest")
        if not isinstance(ratio, list) or not ratio: missing.append("long_short_ratio")
        bids = [(float(p), float(q)) for p, q in (depth or {}).get("bids", [])]
        asks = [(float(p), float(q)) for p, q in (depth or {}).get("asks", [])]
        bid_volume, ask_volume = sum(q for _, q in bids), sum(q for _, q in asks)
        total_depth = bid_volume + ask_volume
        buy_volume = sum(float(t.get("q", 0)) for t in (trades or []) if not t.get("m", False))
        sell_volume = sum(float(t.get("q", 0)) for t in (trades or []) if t.get("m", False))
        source_timestamps = {"candles": latest_candle_times, "mark": (mark or {}).get("time"), "open_interest": (oi or {}).get("time")}
        return {"symbol": symbol, "timestamp": now_ms, "source": "binance_usdm_rest", "source_timestamps": source_timestamps, "price": _number((ticker or {}).get("price")), "mark_price": _number((mark or {}).get("markPrice")), "index_price": _number((mark or {}).get("indexPrice")), "candles": candles, "order_book": {"bid_volume": bid_volume, "ask_volume": ask_volume, "imbalance": (bid_volume - ask_volume) / total_depth if total_depth else None}, "order_flow": {"taker_buy_volume": buy_volume, "taker_sell_volume": sell_volume, "imbalance": (buy_volume-sell_volume)/(buy_volume+sell_volume) if buy_volume + sell_volume else None}, "open_interest": {"value": _number((oi or {}).get("openInterest")), "timestamp": (oi or {}).get("time")}, "funding": {"rate": _number((mark or {}).get("lastFundingRate")), "next_funding_time": (mark or {}).get("nextFundingTime")}, "positioning": {"long_short_ratio": _number((ratio or [{}])[-1].get("longShortRatio"))}, "errors": errors, "quality": {"valid": not missing and not stale, "missing": missing, "stale": stale, "fresh": not stale and not missing, "errors": errors, "max_age_seconds": max_age_ms / 1000}, "btc_context": {}}

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


def derive_structure(candles: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for timeframe, rows in candles.items():
        if len(rows) < 3:
            output[timeframe] = {"bias": "unknown", "structure": "INSUFFICIENT"}
            continue
        closes = [float(row["close"]) for row in rows[-20:]]
        highs = [float(row["high"]) for row in rows[-20:]]
        lows = [float(row["low"]) for row in rows[-20:]]
        higher = closes[-1] > closes[0] and highs[-1] >= max(highs[-5:])
        lower = closes[-1] < closes[0] and lows[-1] <= min(lows[-5:])
        bias = "bullish" if higher else "bearish" if lower else "neutral"
        output[timeframe] = {"bias": bias, "structure": "HH_HL" if higher else "LH_LL" if lower else "RANGE", "last_close": closes[-1], "swing_high": max(highs[-8:]), "swing_low": min(lows[-8:])}
    return output


def decide(snapshot: dict[str, Any]) -> dict[str, Any]:
    structure = derive_structure(snapshot.get("candles", {}))
    quality = snapshot.get("quality", {})
    base = {"id": uuid.uuid4().hex, "symbol": snapshot.get("symbol", "UNKNOWN"), "execution_mode": "PAPER_ONLY", "strategy_version": "foundation-1.0", "market_regime": "UNKNOWN", "structure": structure, "created_at": now_iso()}
    if not quality.get("valid") or not quality.get("fresh") or snapshot.get("price") is None:
        return {**base, "direction": "NO_TRADE", "entry_status": "NO_TRADE", "confidence": 0.0, "reason": "Analysis unavailable: required live market data is missing or stale.", "conflicts": quality.get("missing", []) + quality.get("stale", [])}
    htf = [structure[t]["bias"] for t in ("4h", "1h")]
    ltf = [structure[t]["bias"] for t in ("15m", "5m")]
    conflict = len(set(htf + ltf) - {"neutral"}) > 1
    if conflict or "neutral" in htf:
        return {**base, "direction": "NO_TRADE", "entry_status": "WAIT", "confidence": 0.35, "reason": "Timeframe evidence conflicts; waiting for alignment.", "conflicts": [f"HTF={htf}", f"LTF={ltf}"], "market_regime": "REVERSAL_RISK"}
    direction = "LONG" if htf[0] == "bullish" and all(value == "bullish" for value in ltf) else "SHORT" if htf[0] == "bearish" and all(value == "bearish" for value in ltf) else "NO_TRADE"
    if direction == "NO_TRADE":
        return {**base, "direction": direction, "entry_status": "WAIT", "confidence": 0.4, "reason": "No confirmed lower-timeframe continuation.", "conflicts": [f"HTF={htf}", f"LTF={ltf}"], "market_regime": "RANGING"}
    price, execution = float(snapshot["price"]), structure["15m"]
    stop = execution["swing_low"] if direction == "LONG" else execution["swing_high"]
    risk = abs(price - stop)
    if risk <= 0 or risk / price > 0.1:
        return {**base, "direction": "NO_TRADE", "entry_status": "NO_TRADE", "confidence": 0.0, "reason": "Structure-derived stop is invalid.", "conflicts": []}
    tp1 = price + 2 * risk if direction == "LONG" else price - 2 * risk
    return {**base, "direction": direction, "entry_status": "CONFIRMED", "confidence": 0.65, "reason": "Multi-timeframe structure is aligned; paper decision only.", "conflicts": [], "entry": price, "stop_loss": stop, "take_profits": [tp1], "invalidation": stop, "expected_rr": 2.0, "market_regime": "TRENDING_UP" if direction == "LONG" else "TRENDING_DOWN"}


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
        if created and status in TERMINAL_OUTCOMES: self._postmortem(trade, outcome)
        return outcome, created

    def _postmortem(self, trade: dict[str, Any], outcome: dict[str, Any]) -> None:
        primary = "VALID_SETUP_NORMAL_LOSS" if outcome["status"] == "SL_HIT" else "UNKNOWN"
        payload = {"trade_id": trade["id"], "outcome": outcome["status"], "primary_error": primary, "summary": f"Paper trade closed as {outcome['status']}; strategy was not modified.", "created_at": now_iso()}
        with self.store._lock, self.store._connection() as conn:
            conn.execute("INSERT OR IGNORE INTO post_mortems VALUES (?,?,?)", (trade["id"], json.dumps(payload), now_iso()))
        lesson = self.store.save_learning("lessons", {"trade_id": trade["id"], "category": primary, "condition": trade["market_regime"], "lesson": "Observe repeated evidence before proposing a candidate rule.", "evidence_count": 1})
        self.store.save_learning("patterns", {"trade_id": trade["id"], "description": f"{trade['direction']} in {trade['market_regime']}", "outcome": outcome["status"], "lesson_id": lesson["id"]})
