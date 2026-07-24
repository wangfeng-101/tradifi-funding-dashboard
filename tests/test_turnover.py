import unittest

from scripts.funding_schedule import settlement_is_due
from scripts.turnover import normalize_csv_turnover


class TurnoverCacheTests(unittest.TestCase):
    def test_uses_only_requested_market_rows(self):
        rows = [
            {
                "market": "spot",
                "symbol": "AAPLUSDT",
                "turnover_24h_usdt": "123.45",
            },
            {
                "market": "perp",
                "symbol": "AAPLUSDT",
                "turnover_24h_usdt": "678.90",
            },
            {
                "market": "spot",
                "symbol": "EMPTYUSDT",
                "turnover_24h_usdt": "",
            },
        ]

        self.assertEqual(
            normalize_csv_turnover(rows, market="spot"),
            {"AAPLUSDT": 123.45},
        )

    def test_settlement_due_boundary(self):
        latest = 1_000_000
        interval_ms = 8 * 3_600_000

        self.assertFalse(settlement_is_due(latest, 8, latest + interval_ms - 1))
        self.assertTrue(settlement_is_due(latest, 8, latest + interval_ms))


if __name__ == "__main__":
    unittest.main()
