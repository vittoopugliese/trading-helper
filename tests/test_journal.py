from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent import analyst


class JournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "sessions.db"
        self.path_patch = patch("agent.analyst._sessions_db", return_value=self.db)
        self.path_patch.start()

    def tearDown(self) -> None:
        self.path_patch.stop()
        self.tempdir.cleanup()

    def test_migration_is_idempotent(self) -> None:
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """CREATE TABLE trades (
                    id INTEGER PRIMARY KEY, symbol TEXT, timeframe TEXT, trade_type TEXT,
                    direction TEXT, wave TEXT, entry REAL, stop REAL, target REAL,
                    rr_planned REAL, result TEXT, rr_real REAL, emotion TEXT, notes TEXT,
                    created_at TEXT, closed_at TEXT
                )"""
            )
        conn.close()
        analyst.init_sessions_db()
        analyst.init_sessions_db()
        with sqlite3.connect(self.db) as conn:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        conn.close()
        self.assertIn("of_pattern", columns)
        self.assertIn("elliott_alineado", columns)

    def test_stats_exclude_missed_and_report_missing_r(self) -> None:
        analyst.log_trade("HYPEUSDT", "5m", "scalp", "long", result="win", rr_real=2.0, of_pattern="sweep")
        analyst.log_trade("HYPEUSDT", "5m", "scalp", "short", result="loss", rr_real=-1.0, of_pattern="drain")
        analyst.log_trade("HYPEUSDT", "5m", "scalp", "long", result="breakeven", rr_real=0.0)
        analyst.log_trade("HYPEUSDT", "5m", "scalp", "long", result="loss")
        analyst.log_trade("HYPEUSDT", "5m", "scalp", "long", result="missed")
        stats = analyst.journal_stats()
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["total_records"], 5)
        self.assertEqual(stats["missed"], 1)
        self.assertEqual(stats["win_rate"], 25.0)
        self.assertEqual(stats["win_rate_decisive"], 33.3)
        self.assertEqual(stats["r_sample"], 3)
        self.assertEqual(stats["missing_r"], 1)

    def test_close_rejects_second_close(self) -> None:
        trade_id = analyst.log_trade("BTCUSDT", "15m", "swing", "short")
        analyst.close_trade(trade_id, "win", 2.0)
        with self.assertRaises(analyst.TradeAlreadyClosedError):
            analyst.close_trade(trade_id, "loss", -1.0)


if __name__ == "__main__":
    unittest.main()
