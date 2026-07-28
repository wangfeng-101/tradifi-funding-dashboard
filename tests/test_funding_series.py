import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.funding_series import (
    build_funding_series_payloads,
    write_funding_series_payloads,
)


class FundingSeriesTests(unittest.TestCase):
    def setUp(self):
        self.generated_at = "2026-07-28T00:00:00+00:00"
        self.markets = {
            "demo": {
                "futures": {
                    "ABC": {
                        "symbol": "ABCUSDT",
                        "records": [
                            (datetime(2026, 7, 27, 16, tzinfo=timezone.utc), 0.01),
                            (datetime(2026, 7, 28, 0, tzinfo=timezone.utc), -0.02),
                        ],
                    },
                    "EMPTY": {"symbol": "EMPTYUSDT", "records": []},
                },
                "spots": {},
            }
        }

    def test_builds_compact_timestamp_and_rate_pairs(self):
        payloads = build_funding_series_payloads(self.markets, self.generated_at)

        self.assertEqual(payloads["demo"]["generated_at"], self.generated_at)
        self.assertEqual(
            payloads["demo"]["series"]["ABCUSDT"],
            [[1785168000000, 0.01], [1785196800000, -0.02]],
        )
        self.assertNotIn("EMPTYUSDT", payloads["demo"]["series"])

    def test_writes_one_file_per_exchange(self):
        payloads = build_funding_series_payloads(self.markets, self.generated_at)
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory)
            write_funding_series_payloads(payloads, output_dir)
            written = json.loads((output_dir / "demo.json").read_text(encoding="utf-8"))

        self.assertEqual(written["exchange"], "demo")
        self.assertIn("ABCUSDT", written["series"])


if __name__ == "__main__":
    unittest.main()
