import unittest
from unittest import mock

from tradingagents.dataflows.binance import _fetch_klines, _klines_to_ohlcv_frame
from tradingagents.dataflows.errors import NoMarketDataError, VendorRateLimitError


def _kline(open_time=1715299200000):
    return [
        open_time,
        "100.0",
        "110.0",
        "95.0",
        "105.0",
        "1234.5",
        1715385599999,
        "0",
        0,
        "0",
        "0",
        "0",
    ]


class BinanceDataflowTests(unittest.TestCase):
    def test_klines_convert_to_ohlcv_frame(self):
        df = _klines_to_ohlcv_frame([_kline()])
        self.assertEqual(list(df.columns), ["Date", "Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(df.iloc[0]["Close"], 105.0)

    @mock.patch("tradingagents.dataflows.binance._request_json", return_value=[])
    def test_empty_klines_raise_no_market_data(self, _request):
        with self.assertRaises(NoMarketDataError):
            _fetch_klines("FAKEUSDT", "2024-05-01", "2024-05-10", market="spot")

    @mock.patch("tradingagents.dataflows.binance._request_json", side_effect=VendorRateLimitError("limited"))
    def test_rate_limit_propagates(self, _request):
        with self.assertRaises(VendorRateLimitError):
            _fetch_klines("BTCUSDT", "2024-05-01", "2024-05-10", market="futures")

    @mock.patch("tradingagents.dataflows.binance._request_json", return_value=[_kline()])
    def test_spot_and_futures_urls_differ(self, request_json):
        _fetch_klines("BTCUSDT", "2024-05-01", "2024-05-10", market="spot")
        spot_url = request_json.call_args[0][0]
        _fetch_klines("BTCUSDT", "2024-05-01", "2024-05-10", market="futures")
        futures_url = request_json.call_args[0][0]
        self.assertIn("/api/v3/klines", spot_url)
        self.assertIn("/fapi/v1/klines", futures_url)


if __name__ == "__main__":
    unittest.main()
