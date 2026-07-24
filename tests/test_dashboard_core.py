import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from dashboard_core import WINDOW_DELTAS, WINDOW_ORDER, requested_window_is_complete


class RequestedWindowCompletenessTests(unittest.TestCase):
    def setUp(self):
        self.end = datetime(2026, 7, 24, 0, 0, tzinfo=timezone.utc)

    def test_fixed_window_is_incomplete_when_common_listing_is_inside_window(self):
        requested_start = self.end - timedelta(days=7)
        common_start = self.end - timedelta(days=3)

        self.assertFalse(
            requested_window_is_complete(
                common_start,
                requested_start,
                timedelta(days=7),
            )
        )

    def test_fixed_window_is_complete_when_both_markets_predate_window(self):
        requested_start = self.end - timedelta(days=7)
        common_start = self.end - timedelta(days=30)

        self.assertTrue(
            requested_window_is_complete(
                common_start,
                requested_start,
                timedelta(days=7),
            )
        )

    def test_since_listing_window_uses_history_coverage_instead(self):
        self.assertTrue(
            requested_window_is_complete(
                self.end,
                self.end,
                None,
            )
        )

    def test_dashboard_exposes_three_and_fourteen_day_windows(self):
        self.assertEqual(
            WINDOW_ORDER,
            ("1d", "3d", "7d", "14d", "30d", "since_common_listing"),
        )
        self.assertEqual(WINDOW_DELTAS["3d"], timedelta(days=3))
        self.assertEqual(WINDOW_DELTAS["14d"], timedelta(days=14))


if __name__ == "__main__":
    unittest.main()
