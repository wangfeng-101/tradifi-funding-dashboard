import unittest

from scripts.generate_dashboard_data import preserve_missing_exchange_data


def opportunity(identifier, exchanges, marker):
    return {
        "id": identifier,
        "strategy_type": "cross_perp",
        "underlying": identifier.upper(),
        "pair_id": "-".join(exchanges),
        "exchanges": exchanges,
        "marker": marker,
    }


class PreserveMissingExchangeDataTests(unittest.TestCase):
    def test_keeps_current_healthy_data_and_previous_missing_exchange_data(self):
        payload = {
            "exchanges": {"binance": {}, "kucoin": {}, "bybit": {}},
            "errors": [
                "binance funding history: [Errno 2] No such file or directory: history.csv"
            ],
            "opportunities": [
                opportunity("healthy", ["kucoin", "bybit"], "current"),
                opportunity("stale", ["binance", "kucoin"], "incomplete-current"),
            ],
        }
        previous = {
            "opportunities": [
                opportunity("stale", ["binance", "kucoin"], "previous"),
                opportunity("removed", ["kucoin", "bybit"], "previous"),
            ]
        }

        result = preserve_missing_exchange_data(payload, previous)
        by_id = {item["id"]: item for item in result["opportunities"]}

        self.assertEqual(set(by_id), {"healthy", "stale"})
        self.assertEqual(by_id["healthy"]["marker"], "current")
        self.assertEqual(by_id["stale"]["marker"], "previous")
        self.assertEqual(result["stale_exchanges"], ["binance"])

    def test_does_nothing_when_no_source_file_is_missing(self):
        payload = {
            "exchanges": {"binance": {}},
            "errors": [],
            "opportunities": [opportunity("current", ["binance"], "current")],
        }

        result = preserve_missing_exchange_data(payload, {"opportunities": []})

        self.assertIs(result, payload)
        self.assertNotIn("stale_exchanges", result)


if __name__ == "__main__":
    unittest.main()
