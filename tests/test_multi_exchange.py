import unittest
from unittest.mock import patch

from collectors.multi_exchange.fetch_tradifi_data import discover_okx
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


if __name__ == "__main__":
    unittest.main()
