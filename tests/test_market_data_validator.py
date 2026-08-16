import copy
import unittest
from unittest import mock

import pandas as pd

import tradingagents.dataflows.config as config_module
import tradingagents.default_config as default_config
from tradingagents.dataflows import market_data_validator
from tradingagents.dataflows.config import set_config


def _reset_config():
    config_module._config = copy.deepcopy(default_config.DEFAULT_CONFIG)


def _sample_ohlcv():
    return pd.DataFrame(
        [
            {"Date": pd.Timestamp("2024-05-09"), "Open": 90.0, "High": 91.0, "Low": 89.0, "Close": 90.5, "Volume": 1000},
            {"Date": pd.Timestamp("2024-05-10"), "Open": 91.0, "High": 92.0, "Low": 90.0, "Close": 91.5, "Volume": 2000},
        ]
    )


class MarketDataValidatorTests(unittest.TestCase):
    def setUp(self):
        _reset_config()

    def tearDown(self):
        _reset_config()

    @mock.patch("tradingagents.dataflows.market_data_validator.load_binance_futures_ohlcv", return_value=_sample_ohlcv())
    def test_binance_futures_vendor_uses_futures_loader(self, loader):
        set_config({"data_vendors": {"core_stock_apis": "binance_futures"}})
        result = market_data_validator.build_verified_market_snapshot("BTCUSDT", "2024-05-10")
        loader.assert_called_once_with("BTCUSDT", "2024-05-10")
        self.assertIn("Verified market data snapshot", result)
        self.assertIn("91.50", result)

    @mock.patch("tradingagents.dataflows.market_data_validator.load_ohlcv", return_value=_sample_ohlcv())
    def test_default_uses_yfinance_loader(self, loader):
        result = market_data_validator.build_verified_market_snapshot("AAPL", "2024-05-10")
        loader.assert_called_once_with("AAPL", "2024-05-10")
        self.assertIn("Verified market data snapshot", result)


if __name__ == "__main__":
    unittest.main()
