import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WINDOWS = ("1d", "3d", "7d", "14d", "30d", "since_common_listing")


class StaticDashboardLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.index = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.script = (ROOT / "script.js").read_text(encoding="utf-8")

    def test_main_table_contains_all_period_columns(self):
        for window in WINDOWS:
            self.assertIn(f'data-period="{window}"', self.index)

    def test_period_tabs_and_record_count_column_are_removed(self):
        self.assertNotIn('id="window-tabs"', self.index)
        self.assertNotIn("周期内记录数", self.index)

    def test_each_period_cell_contains_funding_and_annualized_values(self):
        self.assertIn("function periodCellHtml(opportunity, window)", self.script)
        self.assertIn('class="period-value', self.script)
        self.assertIn('class="period-annualized', self.script)

    def test_sort_menu_is_generated_for_every_window(self):
        self.assertIn("state.data.windows.map((window)", self.script)
        self.assertIn('periodOptions("spread_desc")', self.script)
        self.assertIn('periodOptions("spread_asc")', self.script)

    def test_minimum_spread_filter_uses_absolute_value(self):
        self.assertIn("年化差值绝对值下限（%）", self.index)
        self.assertIn(
            "Math.abs(annualizedSignedDiff(windowData)) < state.minSpread",
            self.script,
        )

    def test_long_and_short_turnover_range_filters_are_present(self):
        for element_id in (
            "long-turnover-min",
            "long-turnover-max",
            "short-turnover-min",
            "short-turnover-max",
        ):
            self.assertIn(f'id="{element_id}"', self.index)
        self.assertIn("function turnoverWanForLeg(opportunity, leg)", self.script)
        self.assertIn(
            "isWithinRange(longTurnover, state.longTurnoverMin, state.longTurnoverMax)",
            self.script,
        )
        self.assertIn(
            "isWithinRange(shortTurnover, state.shortTurnoverMin, state.shortTurnoverMax)",
            self.script,
        )

    def test_pagination_supports_direct_page_jump(self):
        self.assertIn('id="page-jump-input"', self.script)
        self.assertIn("data-page-jump", self.script)
        self.assertIn("function jumpToPage()", self.script)
        self.assertIn('event.key !== "Enter"', self.script)
        self.assertIn("targetPage <= totalPages", self.script)


if __name__ == "__main__":
    unittest.main()
