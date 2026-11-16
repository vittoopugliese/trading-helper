from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from agent import elliott_backtest as eb
from agent import elliott_review


def path_candles(waypoints: list[float], step: float = 0.5, flat: int = 30, invert: bool = False) -> pd.DataFrame:
    closes = [100.0] * flat
    for target in waypoints:
        while abs(closes[-1] - target) > 1e-9:
            move = min(step, abs(target - closes[-1]))
            closes.append(closes[-1] + (move if target > closes[-1] else -move))
    closes_arr = np.asarray(closes)
    opens = np.concatenate([[closes_arr[0]], closes_arr[:-1]])
    if invert:
        closes_arr, opens = 200.0 - closes_arr, 200.0 - opens
    highs = np.maximum(opens, closes_arr) + 0.1
    lows = np.minimum(opens, closes_arr) - 0.1
    volume = np.full(len(closes_arr), 100.0)
    taker = np.where(closes_arr >= opens, 60.0, 40.0)
    times = pd.date_range("2026-01-01", periods=len(closes_arr), freq="15min", tz="UTC")
    return pd.DataFrame({
        "open_time": times,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes_arr,
        "volume": volume,
        "close_time": times + pd.Timedelta(minutes=15) - pd.Timedelta(milliseconds=1),
        "quote_volume": volume * 100,
        "trades": np.full(len(closes_arr), 20),
        "taker_buy_base": taker,
        "taker_buy_quote": taker * 100,
    })


# Hprev 104 -> L0 100 -> wave 1 to 110 (breaks 104) -> wave 2 to 103.5 (~0.66) -> wave 3 to 124
WAVES = [104.0, 100.0, 110.0, 103.5, 124.0]


class Elliott12Tests(unittest.TestCase):
    def _cand(self, df: pd.DataFrame, side: str) -> dict:
        cands = [c for c in eb.detect_candidates(df, eb.Elliott12Config()) if c["side"] == side]
        self.assertTrue(cands, f"no {side} candidate")
        return max(cands, key=lambda c: c["w1"])

    def test_long_12_triggers_and_reaches_target(self) -> None:
        cand = self._cand(path_candles(WAVES), "long")
        self.assertAlmostEqual(cand["l0"], 99.9)
        self.assertAlmostEqual(cand["h1"], 110.1)
        self.assertGreater(cand["known_time"], cand["h1_time"])  # pivot only known after confirmation
        b = cand["variant_b"]
        self.assertEqual(b["status"], "triggered")
        self.assertTrue(0.5 <= b["w2_depth"] <= 0.786)
        self.assertEqual(b["exit_reason"], "target")
        self.assertGreater(b["entry_time"], b["decision_time"])
        self.assertTrue(cand["wave3"])
        self.assertEqual(cand["variant_a"]["status"], "filled")

    def test_short_is_mirror_of_long(self) -> None:
        cand = self._cand(path_candles(WAVES, invert=True), "short")
        b = cand["variant_b"]
        self.assertEqual(b["status"], "triggered")
        self.assertGreater(b["stop"], b["entry"])
        self.assertLess(b["target"], b["entry"])
        self.assertEqual(b["exit_reason"], "target")

    def test_decision_does_not_depend_on_future_bars(self) -> None:
        df = path_candles(WAVES)
        full = self._cand(df, "long")["variant_b"]
        cut = df.loc[df["close_time"] <= pd.Timestamp(full["entry_time"]) + pd.Timedelta(minutes=15)]
        prefix = self._cand(cut.reset_index(drop=True), "long")["variant_b"]
        self.assertEqual(prefix["decision_time"], full["decision_time"])
        self.assertAlmostEqual(prefix["entry"], full["entry"])
        self.assertAlmostEqual(prefix["stop"], full["stop"])

    def test_wave2_beyond_0786_is_discarded(self) -> None:
        cand = self._cand(path_candles([104.0, 100.0, 110.0, 101.0, 124.0]), "long")
        self.assertEqual(cand["variant_b"]["status"], "too_deep")


class ReviewTests(unittest.TestCase):
    def test_decision_is_final_and_counted(self) -> None:
        cand = {
            "id": "X-1", "side": "long", "wave3": True,
            "variant_b": {"status": "triggered", "net_r": 2.0, "mfe_r": 3.0, "mae_r": -0.2},
        }
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "elliott12_test"
            run.mkdir()
            (run / "candidates.json").write_text(json.dumps([cand]), encoding="utf-8")
            with mock.patch.object(eb, "latest_run_dir", return_value=run), \
                 mock.patch.object(elliott_review, "db_path", return_value=Path(tmp) / "r.db"), \
                 mock.patch.object(elliott_review, "_payload", return_value={"ok": True}):
                elliott_review._CACHE.update({"dir": None, "candidates": {}})
                elliott_review.decide(elliott_review.token("X-1"), "take", "12_claro", 3, None)
                with self.assertRaises(elliott_review.ReviewError):
                    elliott_review.decide(elliott_review.token("X-1"), "skip", None, None, None)
                stats = elliott_review.stats()
                self.assertEqual(stats["taken"]["trades"], 1)
                self.assertEqual(stats["progress"], {"reviewed": 1, "total": 1})
                elliott_review._CACHE.update({"dir": None, "candidates": {}})


if __name__ == "__main__":
    unittest.main()
