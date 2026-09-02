from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from agent.backtest import BacktestConfig, resample_klines, run_backtest


def candles(count: int = 80) -> pd.DataFrame:
    opens = np.full(count, 100.0)
    closes = np.full(count, 100.1)
    highs = np.full(count, 100.6)
    lows = np.full(count, 99.6)
    volume = np.full(count, 100.0)
    taker = np.full(count, 55.0)
    times = pd.date_range("2026-01-01", periods=count, freq="1min", tz="UTC")
    return pd.DataFrame(
        {
            "open_time": times,
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volume,
            "close_time": times + pd.Timedelta(seconds=59, milliseconds=999),
            "quote_volume": volume * 100,
            "trades": np.full(count, 20),
            "taker_buy_base": taker,
            "taker_buy_quote": taker * 100,
        }
    )


class BacktestTests(unittest.TestCase):
    def test_signal_enters_at_next_open(self) -> None:
        df = candles()
        # Previous delta is negative; confirmation delta is positive and high-volume.
        df.loc[29, "taker_buy_base"] = 40.0
        df.loc[30, ["volume", "taker_buy_base", "open", "close"]] = [300.0, 200.0, 100.0, 100.3]
        df.loc[31, "open"] = 101.0
        df.loc[31:40, "high"] = 105.0
        result = run_backtest(
            "HYPEUSDT",
            df,
            BacktestConfig(
                strategy="volume_delta_flip",
                timeframe="1m",
                reward_risk=1.0,
                fee_rate_per_side=0,
                slippage_rate_per_side=0,
            ),
        )
        trade = next(t for t in result["trades"] if t["signal_time"].startswith("2026-01-01T00:30"))
        self.assertEqual(trade["entry_time"], df.loc[31, "open_time"].isoformat())
        self.assertEqual(trade["entry"], 101.0)

    def test_same_bar_collision_is_stop_first(self) -> None:
        df = candles()
        df.loc[29, "taker_buy_base"] = 40.0
        df.loc[30, ["volume", "taker_buy_base", "open", "close"]] = [300.0, 200.0, 100.0, 100.3]
        df.loc[31, ["open", "high", "low", "close"]] = [100.0, 104.0, 96.0, 100.0]
        result = run_backtest(
            "HYPEUSDT",
            df.iloc[:32],
            BacktestConfig(
                strategy="volume_delta_flip",
                timeframe="1m",
                reward_risk=1.0,
                fee_rate_per_side=0,
                slippage_rate_per_side=0,
            ),
        )
        trade = result["trades"][0]
        self.assertEqual(trade["exit_reason"], "same_bar_stop_first")
        self.assertEqual(trade["gross_r"], -1.0)

    def test_resample_drops_partial_and_gapped_buckets(self) -> None:
        df = candles(11).drop(index=7).reset_index(drop=True)
        out = resample_klines(df, "5m")
        # 00:00 is complete; 00:05 has a missing minute and 00:10 is partial.
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["open_time"], pd.Timestamp("2026-01-01T00:00:00Z"))


if __name__ == "__main__":
    unittest.main()
