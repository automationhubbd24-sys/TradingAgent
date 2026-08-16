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
    assert decide({"symbol": "BTCUSDT", "price": None, "candles": {}, "quality": {"valid": False, "fresh": False, "missing": ["price"]}})["direction"] == "DATA_UNAVAILABLE"
    assert decide({**snapshot(), "quality": {"valid": True, "fresh": False, "missing": [], "stale": ["candles.5m"]}})["direction"] == "DATA_UNAVAILABLE"


def test_decision_engine_v3_waiting_and_confirmed_plan():
    pullback = snapshot(); pullback["candles"]["15m"] = candles("down")["15m"]
    pullback_decision = decide(pullback)
    assert pullback_decision["strategy_version"] == "decision-engine-3.0"
    assert pullback_decision["decision_state"] == "LONG_WAIT"
    assert pullback_decision["directional_bias"] == "LONG"
    assert pullback_decision["entry_status"] == "WAIT_FOR_CONFIRMATION"
    assert not pullback_decision["tradeable"] and "entry" not in pullback_decision

    confirmation = snapshot(); confirmation["candles"]["5m"] = candles("down")["5m"]
    confirmation_decision = decide(confirmation)
    assert confirmation_decision["decision_state"] == "LONG_WAIT"
    assert confirmation_decision["directional_bias"] == "LONG"
    assert confirmation_decision["entry_status"] == "WAIT_FOR_CONFIRMATION"
    assert not confirmation_decision["tradeable"] and "entry" not in confirmation_decision

    confirmed = decide(snapshot())
    assert confirmed["decision_state"] == "LONG_READY" and confirmed["direction"] == "LONG"
    assert confirmed["entry_status"] == "CONFIRMED" and confirmed["tradeable"]
    assert len(confirmed["take_profits"]) == 2 and confirmed["expected_rr"] == 2.0


def test_no_trade_is_distinct_from_data_unavailable():
    no_trade = snapshot(); no_trade["candles"]["1h"] = candles("down")["1h"]
    decision = decide(no_trade)
    assert decision["decision_state"] == "NO_TRADE" and decision["entry_status"] == "NO_TRADE"
    assert decision["direction"] == "NO_TRADE" and not decision["tradeable"]
    unavailable = decide({"symbol": "BTCUSDT", "price": None, "candles": {}, "quality": {"valid": False, "fresh": False, "missing": ["price"]}})
    assert unavailable["decision_state"] == "DATA_UNAVAILABLE" and unavailable["entry_status"] == "UNAVAILABLE"
    assert unavailable["direction"] == "DATA_UNAVAILABLE" and not unavailable["tradeable"]


@pytest.mark.parametrize("direction", ["up", "down"])
def test_outcome_ambiguous_candle_is_non_terminal(tmp_path, direction):
    store = Store(f"sqlite:///{tmp_path / f'{direction}.sqlite'}")
    trade = store.save_decision(decide(snapshot(direction)))
    result, created = OutcomeService(store).evaluate(trade["id"], high=max(trade["take_profits"][0], trade["stop_loss"]) + 1, low=min(trade["take_profits"][0], trade["stop_loss"]) - 1, close=trade["entry"], source="test_ohlc")
    assert created and result["status"] == "UNRESOLVED" and result["source"] == "test_ohlc"
    assert "intrabar order" in result["reason"] and store.postmortem(trade["id"]) is None


def test_outcome_is_idempotent(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'outcomes.sqlite'}"); trade = store.save_decision(decide(snapshot())); service = OutcomeService(store)
    first, created = service.evaluate(trade["id"], high=trade["take_profits"][0] + 1, low=trade["stop_loss"] + 0.1, close=trade["take_profits"][0])
    repeated, repeated_created = service.evaluate(trade["id"], high=trade["take_profits"][0] + 1, low=trade["stop_loss"] + 0.1, close=trade["take_profits"][0])
    assert created and not repeated_created and first == repeated and store.postmortem(trade["id"])


def test_conversation_integrity_and_trade_audit_retention(tmp_path):
    store = Store(f"sqlite:///{tmp_path / 'conversations.sqlite'}"); conversation = store.create_conversation("BTC", "BTCUSDT")
    store.add_message(conversation["id"], "user", "Analyse BTC"); trade = store.save_decision(decide(snapshot()), conversation["id"])
    with pytest.raises(sqlite3.IntegrityError): store.add_message("missing", "user", "no")
    assert store.delete_conversation(conversation["id"]) and store.trade(trade["id"])["id"] == trade["id"]


class Response:
    def __init__(self, payload, status_code=200): self.payload, self.status_code = payload, status_code
    def json(self): return self.payload


def market_get(now_ms, ages=None, missing=()):
    ages = ages or {}
    def rows(interval):
        age = ages.get(interval, 1)
        return [[now_ms - (80 - i) * 1_000, "1", "2", "0.5", "1.5", "10", now_ms - age * 1000] for i in range(80)]
    def fake_get(url, params, timeout):
        if "klines" in url: return Response(rows(params["interval"]))
        if "ticker/price" in url: return Response({} if "ticker" in missing else {"price": "100"})
        if "premiumIndex" in url: return Response({"markPrice": "101", "indexPrice": "99", "lastFundingRate": "0.0001", "time": now_ms})
        if "depth" in url: return Response({"bids": [["100", "3"]], "asks": [["101", "1"]]})
        if "aggTrades" in url: return Response([{ "q": "2", "m": False }, {"q": "1", "m": True}])
        if "openInterest" in url: return Response({"openInterest": "4", "time": now_ms})
        return Response([] if "positioning" in missing else [{"longShortRatio": "1.2"}])
    return fake_get


@pytest.mark.parametrize("interval,age,valid", [("5m", 899, True), ("5m", 901, False), ("15m", 1799, True), ("4h", 7200, True), ("4h", 32401, False)])
def test_per_interval_candle_freshness(monkeypatch, interval, age, valid):
    now_ms = 1_000_000_000
    result = BinanceMarketService(http_get=market_get(now_ms, {interval: age}), clock=lambda: now_ms / 1000).snapshot("BTCUSDT")
    assert result["quality"]["sources"][f"candles.{interval}"]["fresh"] is valid
    assert result["quality"]["valid"] is valid


def test_optional_ratio_missing_does_not_invalidate_and_closed_candle_is_preserved():
    now_ms = 1_000_000_000
    result = BinanceMarketService(http_get=market_get(now_ms, missing=("positioning",)), clock=lambda: now_ms / 1000).snapshot("btc/usdt:usdt")
    assert result["symbol"] == "BTCUSDT" and result["quality"]["valid"]
    assert "positioning" in result["quality"]["optional_missing"]
    assert all(row["close_time"] <= now_ms for row in result["candles"]["5m"])


def test_missing_or_stale_price_produces_data_unavailable():
    now_ms = 1_000_000_000
    result = BinanceMarketService(http_get=market_get(now_ms, missing=("ticker",)), clock=lambda: now_ms / 1000).snapshot("BTCUSDT")
    decision = decide(result)
    assert "ticker" in result["quality"]["critical_missing"]
    assert decision["direction"] == "DATA_UNAVAILABLE" and decision["entry_status"] == "UNAVAILABLE"
    stale = decide({**snapshot(), "quality": {"valid": False, "fresh": False, "critical_missing": [], "critical_stale": ["ticker"], "missing": [], "stale": ["ticker"]}})
    assert stale["direction"] == "DATA_UNAVAILABLE" and stale["entry_status"] == "UNAVAILABLE"


def test_retry_is_bounded_for_transient_errors_and_skips_ordinary_4xx():
    attempts = []
    def transient(url, params, timeout):
        attempts.append(url); return Response({}, 429 if len(attempts) < 3 else 200)
    assert BinanceMarketService(http_get=transient, max_attempts=3, sleeper=lambda _: None)._get("/x", {}) == {}
    assert len(attempts) == 3
    attempts.clear()
    with pytest.raises(RuntimeError):
        BinanceMarketService(http_get=lambda *args, **kwargs: (attempts.append(1) or Response({}, 400)), max_attempts=3, sleeper=lambda _: None)._get("/x", {})
    assert len(attempts) == 1


def test_chat_sse_success_and_degraded_analysis(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'chat.sqlite'}"); monkeypatch.setattr(app_module, "store", store); client = TestClient(app_module.app)
    created = client.post("/api/conversations", json={"title": "Test"}).json()
    assert "event: message_complete" in client.post("/api/chat", json={"conversation_id": created["id"], "message": "hello"}).text
    class GoodMarket:
        def with_btc_context(self, symbol): return snapshot()
    monkeypatch.setattr(app_module, "market", GoodMarket())
    success = client.post("/api/chat", json={"conversation_id": created["id"], "message": "analyse BTCUSDT"})
    assert all(stage in success.text for stage in ("fetching_market_data", "validating_market_data", "loading_4h_structure", "loading_1h_structure", "loading_30m_structure", "loading_15m_structure", "loading_5m_structure", "analyzing_market"))
    class BadMarket:
        def with_btc_context(self, symbol): raise RuntimeError("offline")
    monkeypatch.setattr(app_module, "market", BadMarket())
    degraded = client.post("/api/chat", json={"conversation_id": created["id"], "message": "analyse BTCUSDT"})
    assert "DATA_UNAVAILABLE" in degraded.text and "validation_failure" in degraded.text


def test_manual_evaluation_snapshots_and_performance(tmp_path, monkeypatch):
    store = Store(f"sqlite:///{tmp_path / 'evaluate.sqlite'}"); monkeypatch.setattr(app_module, "store", store); client = TestClient(app_module.app)
    long, short = store.save_decision(decide(snapshot())), store.save_decision(decide(snapshot("down")))
    assert client.post(f"/api/trades/{long['id']}/evaluate", json={"high": long["take_profits"][0] + 1, "low": long["stop_loss"] - 1, "close": long["entry"]}).json()["outcome"]["status"] == "UNRESOLVED"
    assert client.post(f"/api/trades/{short['id']}/evaluate", json={"high": short["stop_loss"] - 1, "low": short["take_profits"][0] - 1, "close": short["take_profits"][0]}).status_code == 200
    assert client.get("/api/performance").json()["open"] == 0
