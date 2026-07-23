import unittest
from unittest.mock import patch

from collectors.binance.fetch_binance_tradifi_listing_times import build_rows
from collectors.binance.fetch_binance_tradifi_symbols import tradifi_spot_symbols


class BinanceListingTimeCacheTests(unittest.TestCase):
    def test_reuses_existing_first_kline_time(self):
        symbols = [
            {
                "symbol": "AAPLUSDT",
                "base_asset": "AAPL",
                "underlying": "AAPL",
                "quote_asset": "USDT",
                "status": "TRADING",
                "is_spot_trading_allowed": "True",
                "is_margin_trading_allowed": "True",
            }
        ]
        existing = [
            {
                "symbol": "AAPLUSDT",
                "spot_first_kline_time_utc": "2025-01-01T00:00:00+00:00",
                "spot_first_kline_time_beijing": "2025-01-01T08:00:00+08:00",
                "spot_first_open": "100",
                "spot_first_close": "101",
                "note": "",
            }
        ]

        with patch(
            "collectors.binance.fetch_binance_tradifi_listing_times.first_spot_kline"
        ) as first_spot_kline:
            rows = build_rows(symbols, existing)

        first_spot_kline.assert_not_called()
        self.assertEqual(
            rows[0]["spot_first_kline_time_utc"],
            "2025-01-01T00:00:00+00:00",
        )
        self.assertEqual(rows[0]["status"], "TRADING")


class BinanceSpotSymbolDiscoveryTests(unittest.TestCase):
    def test_matches_exact_and_b_suffix_symbols(self):
        rows = [
            {"symbol": "AAPLUSDT"},
            {"symbol": "NVDABUSDT"},
            {"symbol": "BTCUSDT"},
        ]

        result = tradifi_spot_symbols(rows, {"AAPL", "NVDA"})

        self.assertEqual(result, ["AAPLUSDT", "NVDABUSDT"])


if __name__ == "__main__":
    unittest.main()
