from web.backend.market_intelligence.advanced_smc import analyze as analyze_smc
from web.backend.market_intelligence.flow import analyze as analyze_flow


def candles(values):
    return [
        {
            "open_time": index,
            "close_time": index + 1,
            "open": value - 0.4,
            "high": value + 1,
            "low": value - 1,
            "close": value,
            "volume": 10 + index,
        }
        for index, value in enumerate(values)
    ]


def test_advanced_smc_exposes_compact_closed_candle_contracts():
    result = analyze_smc(candles([100, 101, 102, 103, 104, 105, 106]), "5m")

    assert result["status"] == "READY"
    assert result["basis"] == "OHLCV_PROXY"
    assert set(result) >= {
        "confirmed_swings",
        "bos",
        "choch",
        "displacement",
        "order_blocks",
        "fvg",
        "support_resistance",
        "breakout",
        "liquidity",
    }
    assert result["displacement"]["grade"] in {"NONE", "WEAK", "MODERATE", "STRONG", "EXTREME"}


def test_flow_derives_aggregate_trade_and_contextual_derivatives_evidence():
    result = analyze_flow(
        {
            "agg_trades": [{"side": "BUY", "quantity": 8}, {"side": "SELL", "quantity": 2}],
            "open_interest": {"value": 120, "history": [100]},
            "funding": {"rate": 0.01, "history": [0.005]},
            "liquidations": [{"side": "SELL", "quantity": 3}],
            "order_book": {"bids": [[100, 9]], "asks": [[101, 3]]},
        }
    )

    assert result["taker_flow"]["state"] == "BUYER_AGGRESSIVE"
    assert result["taker_flow"]["imbalance"] == 0.6
    assert result["open_interest"]["state"] == "OI_RISING"
    assert result["funding"]["state"] == "FUNDING_RISING"
    assert result["liquidations"]["state"] == "LONG_LIQUIDATION_PRESSURE"
    assert result["order_book"]["state"] == "BID_HEAVY"


def test_flow_marks_missing_optional_feeds_unavailable():
    result = analyze_flow({})

    assert result["taker_flow"]["status"] == "UNAVAILABLE"
    assert result["open_interest"]["state"] == "UNAVAILABLE"
    assert result["funding"]["state"] == "UNAVAILABLE"
    assert result["liquidations"]["status"] == "UNAVAILABLE"
    assert result["order_book"]["status"] == "UNAVAILABLE"
