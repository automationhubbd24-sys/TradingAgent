from web.backend.streaming import BinanceFuturesStream, MarketEvent, OrderBook


def test_normalizes_supported_binance_events():
    event = MarketEvent.from_binance({"e": "kline", "E": 10, "s": "BTCUSDT", "k": {"s": "BTCUSDT", "i": "5m", "t": 1, "T": 2, "o": "1", "h": "3", "l": "0", "c": "2", "v": "4", "x": True}}, 3.0)
    assert event and event.event_type == "kline" and event.symbol == "BTCUSDT"
    assert event.data == {"interval": "5m", "open_time": 1, "close_time": 2, "open": "1", "high": "3", "low": "0", "close": "2", "volume": "4", "closed": True}
    assert MarketEvent.from_binance({"e": "unknown", "E": 1, "s": "BTCUSDT"}, 1) is None


def test_order_book_rejects_sequence_gap_and_requests_resync():
    book = OrderBook()
    assert book.apply_snapshot({"lastUpdateId": 10, "bids": [["100", "2"]], "asks": [["101", "3"]]})
    assert book.apply_diff({"U": 11, "u": 11, "pu": 10, "b": [["100", "0"], ["99", "1"]], "a": []})
    assert book.summary()["bids"] == [(99.0, 1.0)]
    assert not book.apply_diff({"U": 13, "u": 13, "pu": 11, "b": [], "a": []})
    assert book.resync_required


def test_combined_stream_state_and_resync_snapshot():
    snapshots = []
    stream = BinanceFuturesStream(["btcusdt"], snapshot_fetcher=lambda symbol: snapshots.append(symbol) or {"lastUpdateId": 7, "bids": [["100", "1"]], "asks": [["101", "1"]]}, clock=lambda: 100.0)
    assert "btcusdt@kline_5m" in stream.url and "btcusdt@depth@100ms" in stream.url and "btcusdt@forceOrder" in stream.url
    assert stream.handle_message({"stream": "btcusdt@depth@100ms", "data": {"e": "depthUpdate", "E": 1, "s": "BTCUSDT", "U": 8, "u": 8, "pu": 7, "b": [["100", "2"]], "a": []}})
    assert snapshots == ["BTCUSDT"]
    assert stream.state("BTCUSDT")["order_book"]["bids"] == [(100.0, 2.0)]
    assert stream.handle_message({"e": "aggTrade", "E": 2, "s": "BTCUSDT", "a": 1, "p": "100", "q": "1", "f": 1, "l": 1, "T": 2, "m": False})
    assert stream.state("BTCUSDT")["agg_trades"][0]["price"] == "100"


def test_status_and_shutdown_are_safe_without_starting_thread():
    stream = BinanceFuturesStream(["BTCUSDT"], clock=lambda: 5.0)
    assert stream.status()["stale"]
    stream.shutdown()
    assert stream.status()["shutdown"] and not stream.status()["connected"]
