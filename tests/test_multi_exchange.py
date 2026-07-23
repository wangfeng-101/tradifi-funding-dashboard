import datetime as dt
import unittest
from unittest.mock import patch

from collectors.multi_exchange.fetch_tradifi_data import (
    UTC,
    discover_bybit,
    discover_okx,
    funding_cutoff,
    funding_gate,
    build_funding_rows,
    merge_funding_rows,
    restore_cached_listing_times,
    retained_funding_rows,
)
from scripts.turnover import normalize


class DiscoverOkxTests(unittest.TestCase):
    def test_includes_stock_spot_and_normalizes_x_prefix(self):
        def fake_http_json(url, params=None, **_kwargs):
            instrument_type = (params or {}).get("instType")
            if "instruments" in url and instrument_type == "SWAP":
                return {"data": []}
            if "tickers" in url and instrument_type == "SWAP":
                return {"data": []}
            if "instruments" in url and instrument_type == "SPOT":
                return {
                    "data": [
                        {
                            "instId": "XAAPL-USDT",
                            "baseCcy": "XAAPL",
                            "quoteCcy": "USDT",
                            "instCategory": "3",
                            "state": "live",
                            "listTime": "1784181600000",
                        },
                        {
                            "instId": "XLM-USDT",
                            "baseCcy": "XLM",
                            "quoteCcy": "USDT",
                            "instCategory": "1",
                            "state": "live",
                            "listTime": "1611907686000",
                        },
                    ]
                }
            if "tickers" in url and instrument_type == "SPOT":
                return {
                    "data": [
                        {
                            "instId": "XAAPL-USDT",
                            "last": "334.76",
                            "bidPx": "334.70",
                            "askPx": "334.80",
                            "volCcy24h": "7524.10",
                        }
                    ]
                }
            raise AssertionError(f"unexpected request: {url} {params}")

        with patch(
            "collectors.multi_exchange.fetch_tradifi_data.http_json",
            side_effect=fake_http_json,
        ):
            rows = discover_okx()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market"], "spot")
        self.assertEqual(rows[0]["symbol"], "XAAPL-USDT")
        self.assertEqual(rows[0]["underlying"], "AAPL")
        self.assertEqual(rows[0]["turnover_24h_usdt"], 7524.10)

    def test_okx_spot_turnover_uses_quote_volume_directly(self):
        result = normalize(
            {"kind": "okx_spot"},
            {
                "code": "0",
                "data": [
                    {
                        "instId": "XAAPL-USDT",
                        "last": "334.76",
                        "volCcy24h": "7524.10",
                    }
                ],
            },
        )

        self.assertEqual(result["XAAPL-USDT"], 7524.10)


class DiscoverBybitTests(unittest.TestCase):
    def test_includes_xstocks_spot_and_removes_x_suffix(self):
        def fake_instruments(category):
            if category == "linear":
                return []
            return [
                {
                    "symbol": "AAPLXUSDT",
                    "baseCoin": "AAPLX",
                    "quoteCoin": "USDT",
                    "symbolType": "xstocks",
                    "status": "Trading",
                    "launchTime": None,
                },
                {
                    "symbol": "BTCUSDT",
                    "baseCoin": "BTC",
                    "quoteCoin": "USDT",
                    "symbolType": "innovation",
                    "status": "Trading",
                },
            ]

        def fake_http_json(url, params=None, **_kwargs):
            category = (params or {}).get("category")
            if "tickers" in url and category == "spot":
                return {
                    "result": {
                        "list": [
                            {
                                "symbol": "AAPLXUSDT",
                                "turnover24h": "125000.50",
                                "lastPrice": "210.25",
                                "bid1Price": "210.20",
                                "ask1Price": "210.30",
                            }
                        ]
                    }
                }
            if "tickers" in url and category == "linear":
                return {"result": {"list": []}}
            raise AssertionError(f"unexpected request: {url} {params}")

        with (
            patch(
                "collectors.multi_exchange.fetch_tradifi_data.bybit_instruments",
                side_effect=fake_instruments,
            ),
            patch(
                "collectors.multi_exchange.fetch_tradifi_data.bybit_spot_listing_time",
                return_value=None,
            ),
            patch(
                "collectors.multi_exchange.fetch_tradifi_data.http_json",
                side_effect=fake_http_json,
            ),
        ):
            rows = discover_bybit()

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["market"], "spot")
        self.assertEqual(rows[0]["symbol"], "AAPLXUSDT")
        self.assertEqual(rows[0]["underlying"], "AAPL")
        self.assertEqual(rows[0]["turnover_24h_usdt"], 125000.50)


class IncrementalFundingTests(unittest.TestCase):
    def test_successful_empty_history_is_not_an_error_row(self):
        market = {
            "exchange": "bybit",
            "symbol": "NEWUSDT",
            "underlying": "NEW",
            "category": "stock",
            "listing_time_utc": "2026-07-23T10:00:00+00:00",
            "listing_time_beijing": "2026-07-23T18:00:00+08:00",
            "funding_interval_hours": 8,
        }

        rows = build_funding_rows(
            market,
            [],
            "",
        )

        self.assertEqual(rows, [])

    def test_gate_ignores_records_before_incremental_cutoff(self):
        cutoff = dt.datetime(2026, 7, 23, 8, 0, 1, tzinfo=UTC)
        payload = [
            {"t": 1784793600, "r": "0.0001"},
            {"t": 1784822400, "r": "0.0002"},
        ]

        with (
            patch(
                "collectors.multi_exchange.fetch_tradifi_data.http_json",
                return_value=payload,
            ),
            patch(
                "collectors.multi_exchange.fetch_tradifi_data.LIMITERS"
            ) as limiters,
        ):
            limiters.__getitem__.return_value.wait.return_value = None
            records = funding_gate({"symbol": "AAPLX_USDT"}, cutoff)

        self.assertEqual(
            records,
            [(dt.datetime(2026, 7, 23, 16, 0, tzinfo=UTC), 0.0002)],
        )

    def test_retains_recent_active_rows_and_resumes_after_latest_record(self):
        history_cutoff = dt.datetime(2026, 6, 20, tzinfo=UTC)
        rows = [
            {
                "symbol": "AAPLUSDT",
                "funding_time_utc": "2026-07-20T08:00:00+00:00",
            },
            {
                "symbol": "AAPLUSDT",
                "funding_time_utc": "2026-06-01T08:00:00+00:00",
            },
            {
                "symbol": "OLDUSDT",
                "funding_time_utc": "2026-07-20T08:00:00+00:00",
            },
        ]

        retained, latest = retained_funding_rows(
            rows,
            {"AAPLUSDT"},
            history_cutoff,
        )
        cutoff = funding_cutoff(
            {"listing_time_utc": "2026-01-01T00:00:00+00:00"},
            history_cutoff,
            latest["AAPLUSDT"],
        )

        self.assertEqual(len(retained), 1)
        self.assertEqual(
            cutoff,
            dt.datetime(2026, 7, 20, 8, 0, 0, 1000, tzinfo=UTC),
        )

    def test_merge_deduplicates_records_and_keeps_current_error(self):
        existing = [
            {
                "symbol": "AAPLUSDT",
                "underlying": "AAPL",
                "funding_time_utc": "2026-07-20T08:00:00+00:00",
                "funding_rate": "0.0001",
                "error": "",
            }
        ]
        new = [
            {
                **existing[0],
                "funding_rate": "0.0002",
            },
            {
                "symbol": "MSFTUSDT",
                "underlying": "MSFT",
                "funding_time_utc": "",
                "funding_rate": "",
                "error": "temporary API failure",
            },
        ]

        merged = merge_funding_rows(existing, new)

        self.assertEqual(len(merged), 2)
        self.assertEqual(merged[0]["funding_rate"], "0.0002")
        self.assertEqual(merged[1]["error"], "temporary API failure")

    @patch.dict(
        "collectors.multi_exchange.fetch_tradifi_data.LISTING_TIME_CACHE",
        {
            ("bybit", "spot", "AAPLXUSDT"): dt.datetime(
                2026, 1, 2, 3, 4, tzinfo=UTC
            )
        },
        clear=True,
    )
    def test_restores_cached_listing_time(self):
        markets = [
            {
                "exchange": "bybit",
                "market": "spot",
                "symbol": "AAPLXUSDT",
                "listing_time_utc": "",
                "listing_time_beijing": "",
            }
        ]

        restore_cached_listing_times(markets)

        self.assertEqual(
            markets[0]["listing_time_utc"],
            "2026-01-02T03:04:00+00:00",
        )
        self.assertEqual(
            markets[0]["listing_time_beijing"],
            "2026-01-02T11:04:00+08:00",
        )

if __name__ == "__main__":
    unittest.main()
