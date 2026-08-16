import unittest

from tradingagents.dataflows.binance import normalize_binance_symbol


class BinanceSymbolUtilsTests(unittest.TestCase):
    def test_direct_usdt_symbols_are_kept(self):
        self.assertEqual(normalize_binance_symbol("BTCUSDT"), "BTCUSDT")
        self.assertEqual(normalize_binance_symbol("1000PEPEUSDT"), "1000PEPEUSDT")
        self.assertEqual(normalize_binance_symbol("WIFUSDT"), "WIFUSDT")

    def test_exchange_style_pairs_are_converted(self):
        self.assertEqual(normalize_binance_symbol("BTC/USDT"), "BTCUSDT")
        self.assertEqual(normalize_binance_symbol("BTC/USDT:USDT"), "BTCUSDT")

    def test_yahoo_style_usd_pairs_convert_to_usdt(self):
        self.assertEqual(normalize_binance_symbol("BTC-USD"), "BTCUSDT")
        self.assertEqual(normalize_binance_symbol("BTCUSD"), "BTCUSDT")


if __name__ == "__main__":
    unittest.main()
