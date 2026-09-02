from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import historical, market_data


def row(open_time: int) -> list[object]:
    return [open_time, "100", "101", "99", "100.5", "10", open_time + 59_999, "1000", 5, "6", "600", "0"]


class HistoricalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "cache.db"
        self.path_patch = patch("agent.market_data._db_path", return_value=self.db)
        self.path_patch.start()
        market_data.init_cache_db()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.tempdir.cleanup()

    def test_coverage_detects_gap_inside_requested_range(self) -> None:
        market_data.save_klines("TESTUSDT", "1m", [row(0), row(120_000)])
        report = historical.coverage_report("TESTUSDT", "1m", 0, 120_000)
        self.assertEqual(report.rows, 2)
        self.assertEqual(report.expected_rows_in_span, 3)
        self.assertEqual(report.missing_rows_in_span, 1)
        self.assertEqual(report.gap_count, 1)
        self.assertFalse(report.complete_to_last_closed_bar)

    def test_download_pages_and_resumes_without_duplicates(self) -> None:
        pages = {
            0: [row(0), row(60_000)],
            120_000: [row(120_000)],
        }
        with (
            patch("agent.historical.discover_first_open_time", return_value=0),
            patch("agent.historical._last_closed_open_ms", return_value=120_000),
            patch("agent.historical._fetch_page_with_retry", side_effect=lambda _s, _sym, _int, cursor, _end, _retries: pages.get(cursor, [])),
        ):
            first = historical.download_complete_history("TESTUSDT", "1m", use_archive=False, pause_seconds=0)
            second = historical.download_complete_history("TESTUSDT", "1m", use_archive=False, pause_seconds=0)
        self.assertEqual(first.coverage.rows, 3)
        self.assertTrue(first.coverage.complete_to_last_closed_bar)
        self.assertEqual(second.rows_received, 0)
        self.assertEqual(second.coverage.rows, 3)


if __name__ == "__main__":
    unittest.main()
