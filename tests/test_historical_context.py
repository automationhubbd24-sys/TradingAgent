from __future__ import annotations

from threading import Event
from time import sleep

from web.backend.market_intelligence.historical_context import HistoricalProfileBuilder, build


def candles(count: int = 80) -> list[dict]:
    return [
        {
            "open_time": index * 1000,
            "close_time": index * 1000 + 999,
            "open": 100 + index - 0.5,
            "high": 100 + index + 1,
            "low": 100 + index - 1,
            "close": 100 + index,
            "volume": 10,
        }
        for index in range(count)
    ]


def snapshot(rows: list[dict]) -> dict:
    return {
        "symbol": "BTCUSDT",
        "source": "binance_usdm_rest",
        "timestamp": rows[-1]["close_time"],
        "price": rows[-1]["close"],
        "candles": {"4h": rows},
    }


def test_profile_is_futures_only_and_excludes_future_candles():
    rows = candles()
    future = {**rows[-1], "open_time": 999999, "close_time": 1000000, "close": 10000, "high": 10001, "low": 9999}
    result = build(snapshot(rows), {"4h": [*rows, future]})
    assert result["status"] == "READY"
    assert result["sample_size"] == len(rows)
    assert result["as_of"] == rows[-1]["close_time"]
    assert result["current_price_map"]["range_high"] < 1000
    assert result["analogues"]["status"] == "AVAILABLE"
    assert build({"symbol": "BTCUSDT", "price": 100, "candles": {"4h": rows}})["status"] == "UNAVAILABLE"


def test_analogues_are_sample_size_gated():
    result = build(snapshot(candles(32)))
    assert result["analogues"]["status"] == "INSUFFICIENT_HISTORY"
    assert result["analogues"]["sample_size"] == 0


def test_full_history_profile_build_does_not_block_partial_fallback():
    rows = candles(30)
    release = Event()

    def loader(_: str) -> dict:
        release.wait(1)
        return {"4h": candles()}

    current = snapshot(rows)
    current["timestamp"] = candles()[-1]["close_time"]
    current["price"] = candles()[-1]["close"]
    builder = HistoricalProfileBuilder()
    try:
        initial = builder.profile(current, loader)
        assert initial["status"] == "BUILDING"
        assert initial["fallback"] == "PARTIAL_CURRENT_HISTORY"
        release.set()
        completed = initial
        for _ in range(50):
            completed = builder.profile(current, loader)
            if completed["status"] == "READY":
                break
            sleep(0.01)
        assert completed["status"] == "READY"
        assert completed["sample_size"] == 80
    finally:
        builder.shutdown()
