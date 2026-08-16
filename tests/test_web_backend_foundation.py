import sqlite3

import pytest
from fastapi.testclient import TestClient

from web.backend import app as app_module
from web.backend.chat import parse_intent
from web.backend.foundation import BinanceMarketService, OutcomeService, Store, database_url, decide, derive_structure


def candles(direction: str):
    output = {}
    for timeframe in ("4h", "1h", "30m", "15m", "5m"):
        values = list(range(100, 130)) if direction == "up" else list(range(130, 100, -1))
        output[timeframe] = [{"open": value - 1, "high": value + 1, "low": value - 2, "close": value, "volume": 10} for value in values]
    return output


def snapshot(direction="up"):
    return {"symbol": "BTCUSDT", "price": 129.0 if direction == "up" else 101.0, "candles": candles(direction), "quality": {"valid": True, "fresh": True, "missing": []}}


def test_structure_and_quality_no_trade():
    assert derive_structure(candles("up"))["4h"]["bias"] == "bullish"
    assert decide({"symbol": "BTCUSDT", "price": None, "candles": {}, "quality": {"valid": False, "fresh": False, "missing": ["price"]}})["direction"] == "NO_TRADE"
    assert decide({**snapshot(), "quality": {"valid": True, "fresh": False, "missing": [], "stale": ["candles.5m"]}})["direction"] == "NO_TRADE"


def test_decision_conflict_and_paper_long():
    conflicting = snapshot()
    conflicting["candles"]["5m"] = candles("down")["5m"]
    assert decide(conflicting)["direction"] == "NO_TRADE"
    result = decide(snapshot())
    assert result["direction"] == "LONG"
    assert result["execution_mode"] == "PAPER_ONLY"


@pytest.mark.parametrize("direction", ["up", "down"])
def test_outcome_ambiguous_candle_is_non_terminal(tmp_path, direction):
    store = Store(f"sqlite:///{tmp_path / f'{direction}.sqlite'}")
    trade = store.save_decision(decide(snapshot(direction)))
    result, created = OutcomeService(store).evaluate(trade["id"], high=max(trade["take_profits"][0], trade["stop_loss"]) + 1, low=min(trade["take_profits"][0], trade["stop_loss"]) - 1, close=trade["entry"], source="test_ohlc")
    assert created and result["status"] == "UNRESOLVED"
    assert result["source"] == "test_ohlc"
    assert "intrabar order" in result["reason"]
    assert store.postmortem(trade["id"]) is None


def test_outcome_is_idempotent(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'outcomes.sqlite'}")
    trade = store.save_decision(decide(snapshot()))
    service = OutcomeService(store)
    first, created = service.evaluate(trade["id"], high=trade["take_profits"][0] + 1, low=trade["stop_loss"] + 0.1, close=trade["take_profits"][0])
    repeated, repeated_created = service.evaluate(trade["id"], high=trade["take_profits"][0] + 1, low=trade["stop_loss"] + 0.1, close=trade["take_profits"][0])
    assert created and not repeated_created
    assert first == repeated
    assert store.postmortem(trade["id"])


def test_conversation_integrity_and_trade_audit_retention(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'conversations.sqlite'}")
    conversation = store.create_conversation("BTC", "BTCUSDT")
    store.add_message(conversation["id"], "user", "Analyse BTC")
    trade = store.save_decision(decide(snapshot()), conversation["id"])
    with pytest.raises(sqlite3.IntegrityError):
        store.add_message("missing", "user", "no")
    assert store.delete_conversation(conversation["id"])
    assert store.trade(trade["id"])["id"] == trade["id"]
    with store._connection() as conn:
        assert conn.execute("SELECT conversation_id FROM trade_decisions WHERE id=?", (trade["id"],)).fetchone()[0] is None
        assert conn.execute("SELECT COUNT(*) FROM messages WHERE conversation_id=?", (conversation["id"],)).fetchone()[0] == 0


def test_market_normalization_freshness_and_closed_candles(monkeypatch):
    class Response:
        status_code = 200
        def __init__(self, payload): self.payload = payload
        def json(self): return self.payload
    now_ms = 1_000_000
    rows = [[now_ms - (80 - i) * 1_000, "1", "2", "0.5", "1.5", "10", now_ms - (79 - i) * 1_000] for i in range(80)]
    rows[-1][6] = now_ms + 1_000
    def fake_get(url, params, timeout):
        if "klines" in url: return Response(rows)
        if "ticker/price" in url: return Response({"price": "100"})
        if "premiumIndex" in url: return Response({"markPrice": "101", "indexPrice": "99", "lastFundingRate": "0.0001", "time": now_ms})
        if "depth" in url: return Response({"bids": [["100", "3"]], "asks": [["101", "1"]]})
        if "aggTrades" in url: return Response([{ "q": "2", "m": False }, {"q": "1", "m": True}])
        if "openInterest" in url: return Response({"openInterest": "4", "time": now_ms})
        return Response([{ "longShortRatio": "1.2" }])
    monkeypatch.setenv("TRADINGAGENTS_MARKET_MAX_AGE_SECONDS", "60")
    result = BinanceMarketService(http_get=fake_get, clock=lambda: now_ms / 1000).snapshot("btc/usdt:usdt")
    assert result["symbol"] == "BTCUSDT"
    assert result["order_book"]["imbalance"] == 0.5
    assert result["order_flow"]["imbalance"] == 1 / 3
    assert result["candles"]["5m"][-1]["close_time"] <= now_ms
    assert result["quality"]["fresh"]
    stale = BinanceMarketService(http_get=fake_get, clock=lambda: (now_ms + 120_000) / 1000).snapshot("BTCUSDT")
    assert not stale["quality"]["fresh"]


def test_chat_sse_success_and_degraded_analysis(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'chat.sqlite'}")
    monkeypatch.setattr(app_module, "store", store)
    client = TestClient(app_module.app)
    created = client.post("/api/conversations", json={"title": "Test"}).json()
    ordinary = client.post("/api/chat", json={"conversation_id": created["id"], "message": "hello"})
    assert ordinary.status_code == 200
    assert "event: message_start" in ordinary.text and "event: message_complete" in ordinary.text

    class GoodMarket:
        def with_btc_context(self, symbol): return snapshot()
    monkeypatch.setattr(app_module, "market", GoodMarket())
    success = client.post("/api/chat", json={"conversation_id": created["id"], "message": "analyse BTCUSDT"})
    assert "event: market_data" in success.text and "event: analysis_result" in success.text

    class BadMarket:
        def with_btc_context(self, symbol): raise RuntimeError("offline")
    monkeypatch.setattr(app_module, "market", BadMarket())
    degraded = client.post("/api/chat", json={"conversation_id": created["id"], "message": "analyse BTCUSDT"})
    assert "event: analysis_result" in degraded.text and "offline" in degraded.text


def test_manual_evaluation_snapshots_and_performance(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'evaluate.sqlite'}")
    monkeypatch.setattr(app_module, "store", store)
    client = TestClient(app_module.app)
    long = store.save_decision(decide(snapshot()))
    short = store.save_decision(decide(snapshot("down")))
    ambiguous = client.post(f"/api/trades/{long['id']}/evaluate", json={"high": long["take_profits"][0] + 1, "low": long["stop_loss"] - 1, "close": long["entry"]})
    assert ambiguous.status_code == 200
    assert ambiguous.json()["outcome"]["status"] == "UNRESOLVED"
    terminal = client.post(f"/api/trades/{short['id']}/evaluate", json={"high": short["stop_loss"] - 1, "low": short["take_profits"][0] - 1, "close": short["take_profits"][0]})
    assert terminal.status_code == 200
    with store._connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trade_snapshots WHERE trade_id=?", (long["id"],)).fetchone()[0] == 1
    performance = client.get("/api/performance").json()
    assert performance["open"] == 0 and performance["unresolved"] == 1 and performance["terminal"] == 1
    assert client.post(f"/api/trades/{long['id']}/evaluate", json={"high": 1, "low": 2, "close": 1}).status_code == 422
