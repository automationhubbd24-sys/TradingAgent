import unittest

from web.backend.jobs import _build_config, _normalize_request
from web.backend.schemas import AnalyzeRequest


class WebBinanceConfigTests(unittest.TestCase):
    def test_binance_futures_sets_market_vendors(self):
        request = _normalize_request(
            AnalyzeRequest(
                ticker="BTC/USDT:USDT",
                asset_type="crypto",
                market_data_vendor="binance_futures",
                analysts=["market", "fundamentals"],
            )
        )
        config = _build_config(request)
        self.assertEqual(request.ticker, "BTCUSDT")
        self.assertEqual(config["data_vendors"]["core_stock_apis"], "binance_futures")
        self.assertEqual(config["data_vendors"]["technical_indicators"], "binance_futures")
        self.assertNotIn("fundamentals", request.analysts)

    def test_yahoo_stock_keeps_fundamentals(self):
        request = _normalize_request(
            AnalyzeRequest(
                ticker="AAPL",
                asset_type="stock",
                market_data_vendor="yfinance",
                analysts=["market", "fundamentals"],
            )
        )
        config = _build_config(request)
        self.assertEqual(config["data_vendors"]["core_stock_apis"], "yfinance")
        self.assertIn("fundamentals", request.analysts)


if __name__ == "__main__":
    unittest.main()
