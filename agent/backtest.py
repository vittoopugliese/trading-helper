"""Causal OHLCV+taker-delta backtesting for the user's volume setups.

Signals are evaluated only after a candle closes and entries happen at the next
candle open.  Footprint POC, DOM and historical resting liquidity are deliberately
not approximated: Binance klines do not contain them.
"""

from __future__ import annotations

import json
import math
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from agent import historical, market_data
from agent.config import resolve_path

STRATEGIES = (
    "volume_delta_flip",
    "sweep_reclaim",
    "sfp_strict_proxy",
    "absorption_reversal",
    "delta_drain",
    "cvd_divergence",
    "balance_rotation",
    "balance_breakout",
    "balance_fakeout",
    "order_block_retest_proxy",
    "a_plus_composite",
)

PANDAS_FREQ = {
    "1m": "1min",
    "3m": "3min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "2h": "2h",
    "4h": "4h",
    "6h": "6h",
    "8h": "8h",
    "12h": "12h",
    "1d": "1D",
}


@dataclass(frozen=True)
class BacktestConfig:
    strategy: str
    timeframe: str = "5m"
    reward_risk: float = 2.0
    volume_ratio: float = 1.5
    poi_lookback: int = 30
    atr_stop_multiplier: float = 1.5
    max_hold_bars: int = 96
    fee_rate_per_side: float = 0.0005
    slippage_rate_per_side: float = 0.0002
    same_bar_policy: str = "stop_first"
    btc_filter: bool = False
    train_fraction: float = 0.70
    side: str = "both"


def resample_klines(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Build higher-timeframe Binance-compatible candles from cached 1m data."""
    if timeframe == "1m":
        return df.copy().reset_index(drop=True)
    if timeframe not in PANDAS_FREQ:
        raise ValueError(f"Unsupported timeframe: {timeframe}")
    if df.empty:
        return df.copy()
    indexed = df.set_index("open_time")
    aggregations = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "close_time": "max",
        "quote_volume": "sum",
        "trades": "sum",
        "taker_buy_base": "sum",
        "taker_buy_quote": "sum",
    }
    resampler = indexed.resample(PANDAS_FREQ[timeframe], label="left", closed="left")
    out = resampler.agg(aggregations)
    counts = resampler["open"].count()
    expected_count = market_data.INTERVAL_MS[timeframe] // market_data.INTERVAL_MS["1m"]
    # A partial edge bucket or a bucket containing a source gap is not a valid
    # higher-timeframe candle and must never be silently fabricated.
    out = out.loc[counts == expected_count]
    out = out.dropna(subset=["open", "high", "low", "close"])
    return out.reset_index()


def load_history(
    symbol: str,
    timeframe: str,
    start_ms: int | None = None,
    end_ms: int | None = None,
    base_interval: str = "1m",
) -> pd.DataFrame:
    raw = market_data.load_cached_klines_df(symbol, base_interval, start_ms, end_ms)
    if raw.empty:
        raise ValueError(
            f"No cached {base_interval} history for {symbol.upper()}. "
            "Run `python main.py history-download` first."
        )
    now = pd.Timestamp.now(tz="UTC")
    raw = raw.loc[raw["close_time"] < now].copy()
    return resample_klines(raw, timeframe)


def add_features(df: pd.DataFrame, volume_ratio: float, poi_lookback: int) -> pd.DataFrame:
    """Create vectorized, past-only features shared by every setup."""
    out = df.copy().reset_index(drop=True)
    out["delta"] = 2.0 * out["taker_buy_base"] - out["volume"]
    out["buy_ratio"] = np.where(out["volume"] > 0, out["taker_buy_base"] / out["volume"], 0.5)
    out["volume_ma20"] = out["volume"].shift(1).rolling(20, min_periods=20).mean()
    out["volume_ratio"] = out["volume"] / out["volume_ma20"]
    out["volume_spike"] = out["volume_ratio"] >= volume_ratio
    out["cvd"] = out["delta"].cumsum()

    previous_close = out["close"].shift(1)
    true_range = pd.concat(
        [
            out["high"] - out["low"],
            (out["high"] - previous_close).abs(),
            (out["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["atr"] = true_range.rolling(14, min_periods=14).mean()
    out["ema20"] = out["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    out["ema50"] = out["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    out["support"] = out["low"].shift(1).rolling(poi_lookback, min_periods=poi_lookback).min()
    out["resistance"] = out["high"].shift(1).rolling(poi_lookback, min_periods=poi_lookback).max()
    candle_range = (out["high"] - out["low"]).replace(0, np.nan)
    out["close_location"] = (out["close"] - out["low"]) / candle_range
    out["lower_wick_ratio"] = (np.minimum(out["open"], out["close"]) - out["low"]) / candle_range
    out["upper_wick_ratio"] = (out["high"] - np.maximum(out["open"], out["close"])) / candle_range
    return out


def _strategy_signals(df: pd.DataFrame, strategy: str) -> tuple[pd.Series, pd.Series]:
    """Return causal long/short signals stamped at confirmation-candle close."""
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown strategy {strategy!r}; choose one of {', '.join(STRATEGIES)}")
    delta = df["delta"]
    vol = df["volume_spike"].fillna(False)
    bullish_candle = df["close"] > df["open"]
    bearish_candle = df["close"] < df["open"]

    if strategy == "volume_delta_flip":
        active_vol = vol | vol.shift(1, fill_value=False)
        long_signal = (delta > 0) & (delta.shift(1) < 0) & active_vol & bullish_candle
        short_signal = (delta < 0) & (delta.shift(1) > 0) & active_vol & bearish_candle

    elif strategy == "sweep_reclaim":
        prior_long_sweep = (
            (df["low"].shift(1) < df["support"].shift(1))
            & (df["close"].shift(1) > df["support"].shift(1))
            & vol.shift(1, fill_value=False)
            & (delta.shift(1) < 0)
        )
        prior_short_sweep = (
            (df["high"].shift(1) > df["resistance"].shift(1))
            & (df["close"].shift(1) < df["resistance"].shift(1))
            & vol.shift(1, fill_value=False)
            & (delta.shift(1) > 0)
        )
        long_signal = prior_long_sweep & (delta > 0) & bullish_candle & (df["close"] > df["close"].shift(1))
        short_signal = prior_short_sweep & (delta < 0) & bearish_candle & (df["close"] < df["close"].shift(1))

    elif strategy == "sfp_strict_proxy":
        # A kline-only SFP proxy. OI liquidation and footprint volume-at-price
        # are intentionally absent and the name keeps that limitation visible.
        long_signal = (
            (df["low"] < df["support"])
            & (df["close"] > df["support"])
            & (df["lower_wick_ratio"] >= 0.30)
            & vol
            & (delta < 0)
        )
        short_signal = (
            (df["high"] > df["resistance"])
            & (df["close"] < df["resistance"])
            & (df["upper_wick_ratio"] >= 0.30)
            & vol
            & (delta > 0)
        )

    elif strategy == "absorption_reversal":
        tolerance = 0.001
        prior_long_absorption = (
            (df["low"].shift(1) <= df["support"].shift(1) * (1 + tolerance))
            & (df["close"].shift(1) >= df["support"].shift(1))
            & (df["lower_wick_ratio"].shift(1) >= 0.25)
            & (df["close_location"].shift(1) >= 0.50)
            & vol.shift(1, fill_value=False)
            & (delta.shift(1) < 0)
        )
        prior_short_absorption = (
            (df["high"].shift(1) >= df["resistance"].shift(1) * (1 - tolerance))
            & (df["close"].shift(1) <= df["resistance"].shift(1))
            & (df["upper_wick_ratio"].shift(1) >= 0.25)
            & (df["close_location"].shift(1) <= 0.50)
            & vol.shift(1, fill_value=False)
            & (delta.shift(1) > 0)
        )
        long_signal = prior_long_absorption & (delta > 0) & bullish_candle
        short_signal = prior_short_absorption & (delta < 0) & bearish_candle

    elif strategy == "delta_drain":
        d1, d2, d3 = delta.shift(1), delta.shift(2), delta.shift(3)
        at_high = df["high"].shift(1) >= df["resistance"].shift(1) * 0.999
        at_low = df["low"].shift(1) <= df["support"].shift(1) * 1.001
        buyers_draining = (d3 > d2) & (d2 > d1) & (d1 > 0)
        sellers_draining = (d3 < d2) & (d2 < d1) & (d1 < 0)
        short_signal = at_high & buyers_draining & (delta < 0) & bearish_candle
        long_signal = at_low & sellers_draining & (delta > 0) & bullish_candle

    elif strategy == "cvd_divergence":
        half = 5
        recent_low = df["low"].shift(1).rolling(half).min()
        older_low = df["low"].shift(half + 1).rolling(half).min()
        recent_high = df["high"].shift(1).rolling(half).max()
        older_high = df["high"].shift(half + 1).rolling(half).max()
        recent_cvd_low = df["cvd"].shift(1).rolling(half).min()
        older_cvd_low = df["cvd"].shift(half + 1).rolling(half).min()
        recent_cvd_high = df["cvd"].shift(1).rolling(half).max()
        older_cvd_high = df["cvd"].shift(half + 1).rolling(half).max()
        # The user's convention: price HL + CVD LL traps shorts; price LH + CVD HH traps longs.
        long_signal = (recent_low > older_low) & (recent_cvd_low < older_cvd_low) & (delta > 0) & bullish_candle
        short_signal = (recent_high < older_high) & (recent_cvd_high > older_cvd_high) & (delta < 0) & bearish_candle

    elif strategy == "balance_rotation":
        band = (df["resistance"] - df["support"]).replace(0, np.nan)
        near_low = df["close"] <= df["support"] + 0.20 * band
        near_high = df["close"] >= df["resistance"] - 0.20 * band
        long_signal = near_low & (delta > 0) & (delta.shift(1) < 0) & bullish_candle & (df["volume_ratio"] >= 1.0)
        short_signal = near_high & (delta < 0) & (delta.shift(1) > 0) & bearish_candle & (df["volume_ratio"] >= 1.0)

    elif strategy == "balance_breakout":
        long_signal = (df["close"] > df["resistance"]) & vol & (delta > 0) & bullish_candle
        short_signal = (df["close"] < df["support"]) & vol & (delta < 0) & bearish_candle

    elif strategy == "balance_fakeout":
        quiet = df["volume_ratio"] <= 1.0
        long_signal = (
            (df["low"] < df["support"])
            & (df["close"] > df["support"])
            & quiet
            & (delta > 0)
            & bullish_candle
        )
        short_signal = (
            (df["high"] > df["resistance"])
            & (df["close"] < df["resistance"])
            & quiet
            & (delta < 0)
            & bearish_candle
        )

    elif strategy == "order_block_retest_proxy":
        return _order_block_retest_signals(df)

    else:  # a_plus_composite
        sweep_long, sweep_short = _strategy_signals(df, "sweep_reclaim")
        absorption_long, absorption_short = _strategy_signals(df, "absorption_reversal")
        long_signal = (sweep_long | absorption_long) & (df["ema20"] > df["ema50"])
        short_signal = (sweep_short | absorption_short) & (df["ema20"] < df["ema50"])

    return long_signal.fillna(False), short_signal.fillna(False)


def _order_block_retest_signals(df: pd.DataFrame, expiry_bars: int = 30) -> tuple[pd.Series, pd.Series]:
    """Causal order-block proxy: last opposite candle before a volume/BOS impulse."""
    long_signal = pd.Series(False, index=df.index)
    short_signal = pd.Series(False, index=df.index)
    bullish_zone: tuple[float, float, int] | None = None
    bearish_zone: tuple[float, float, int] | None = None
    for i in range(3, len(df)):
        atr = float(df.iloc[i]["atr"])
        if not np.isfinite(atr) or atr <= 0:
            continue
        row = df.iloc[i]
        impulse_range = float(row["high"] - row["low"])
        if (
            row["close"] > row["resistance"]
            and row["delta"] > 0
            and bool(row["volume_spike"])
            and impulse_range >= 1.5 * atr
        ):
            for j in range(i - 1, max(-1, i - 4), -1):
                candle = df.iloc[j]
                if candle["close"] < candle["open"]:
                    bullish_zone = (float(candle["low"]), float(candle["high"]), i)
                    break
        if (
            row["close"] < row["support"]
            and row["delta"] < 0
            and bool(row["volume_spike"])
            and impulse_range >= 1.5 * atr
        ):
            for j in range(i - 1, max(-1, i - 4), -1):
                candle = df.iloc[j]
                if candle["close"] > candle["open"]:
                    bearish_zone = (float(candle["low"]), float(candle["high"]), i)
                    break
        if bullish_zone:
            low, high, confirmed_at = bullish_zone
            if i > confirmed_at and i - confirmed_at <= expiry_bars and row["low"] <= high and row["close"] > high and row["delta"] > 0:
                long_signal.iloc[i] = True
                bullish_zone = None
            elif i - confirmed_at > expiry_bars or row["close"] < low:
                bullish_zone = None
        if bearish_zone:
            low, high, confirmed_at = bearish_zone
            if i > confirmed_at and i - confirmed_at <= expiry_bars and row["high"] >= low and row["close"] < low and row["delta"] < 0:
                short_signal.iloc[i] = True
                bearish_zone = None
            elif i - confirmed_at > expiry_bars or row["close"] > high:
                bearish_zone = None
    return long_signal, short_signal


def _apply_btc_filter(
    df: pd.DataFrame,
    btc_df: pd.DataFrame | None,
    long_signal: pd.Series,
    short_signal: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    if btc_df is None:
        raise ValueError("btc_filter=True requires a matching BTCUSDT dataframe")
    btc = (
        btc_df
        if {"ema20", "ema50"}.issubset(btc_df.columns)
        else add_features(btc_df, volume_ratio=1.0, poi_lookback=20)
    ).set_index("open_time")
    aligned = btc[["ema20", "ema50"]].reindex(df["open_time"], method="ffill")
    bull = (aligned["ema20"] > aligned["ema50"]).to_numpy()
    bear = (aligned["ema20"] < aligned["ema50"]).to_numpy()
    return long_signal & bull, short_signal & bear


def _trade_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "win_rate": None,
            "win_rate_ci95": [None, None],
            "avg_r": None,
            "expectancy_r": None,
            "profit_factor": None,
            "max_drawdown_r": None,
            "max_loss_streak": 0,
            "avg_mfe_r": None,
            "avg_mae_r": None,
        }
    r_values = np.asarray([float(t["net_r"]) for t in trades], dtype=float)
    wins = int((r_values > 1e-9).sum())
    losses = int((r_values < -1e-9).sum())
    breakeven = len(trades) - wins - losses
    decided = wins + losses
    win_rate = wins / decided if decided else 0.0
    if decided:
        z = 1.959963984540054
        denom = 1 + z * z / decided
        center = (win_rate + z * z / (2 * decided)) / denom
        spread = z * math.sqrt(win_rate * (1 - win_rate) / decided + z * z / (4 * decided**2)) / denom
        ci = [round(100 * max(0.0, center - spread), 2), round(100 * min(1.0, center + spread), 2)]
    else:
        ci = [None, None]
    gross_profit = float(r_values[r_values > 0].sum())
    gross_loss = float(-r_values[r_values < 0].sum())
    equity = np.cumsum(r_values)
    running_peak = np.maximum.accumulate(np.insert(equity, 0, 0.0))[1:]
    max_dd = float(np.max(running_peak - equity))
    max_losing_streak = streak = 0
    for value in r_values:
        streak = streak + 1 if value < 0 else 0
        max_losing_streak = max(max_losing_streak, streak)
    return {
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": round(win_rate * 100, 2),
        "win_rate_ci95": ci,
        "avg_r": round(float(r_values.mean()), 4),
        "expectancy_r": round(float(r_values.mean()), 4),
        "median_r": round(float(np.median(r_values)), 4),
        "profit_factor": round(gross_profit / gross_loss, 4) if gross_loss else None,
        "net_r": round(float(r_values.sum()), 4),
        "max_drawdown_r": round(max_dd, 4),
        "max_loss_streak": max_losing_streak,
        "avg_mfe_r": round(float(np.mean([t["mfe_r"] for t in trades])), 4),
        "avg_mae_r": round(float(np.mean([t["mae_r"] for t in trades])), 4),
    }


def run_backtest(
    symbol: str,
    df: pd.DataFrame,
    config: BacktestConfig,
    btc_df: pd.DataFrame | None = None,
    _featured: pd.DataFrame | None = None,
    _signals: tuple[pd.Series, pd.Series] | None = None,
) -> dict[str, Any]:
    """Simulate one strategy with one-position-at-a-time and conservative fills."""
    if config.reward_risk <= 0 or config.max_hold_bars < 1:
        raise ValueError("reward_risk and max_hold_bars must be positive")
    if config.side not in ("both", "long", "short"):
        raise ValueError("side must be both, long or short")
    if df.empty:
        raise ValueError("Cannot backtest an empty dataframe")
    featured = _featured if _featured is not None else add_features(df, config.volume_ratio, config.poi_lookback)
    if _signals is None:
        long_signal, short_signal = _strategy_signals(featured, config.strategy)
        if config.btc_filter:
            long_signal, short_signal = _apply_btc_filter(featured, btc_df, long_signal, short_signal)
    else:
        long_signal, short_signal = _signals
    if config.side == "long":
        short_signal = pd.Series(False, index=short_signal.index)
    elif config.side == "short":
        long_signal = pd.Series(False, index=long_signal.index)

    candidates = np.flatnonzero((long_signal | short_signal).to_numpy())
    opens = featured["open"].to_numpy(dtype=float)
    highs = featured["high"].to_numpy(dtype=float)
    lows = featured["low"].to_numpy(dtype=float)
    closes = featured["close"].to_numpy(dtype=float)
    atrs = featured["atr"].to_numpy(dtype=float)
    volume_ratio_values = featured["volume_ratio"].to_numpy(dtype=float)
    delta_values = featured["delta"].to_numpy(dtype=float)
    open_times = featured["open_time"].tolist()
    close_times = featured["close_time"].tolist()
    trades: list[dict[str, Any]] = []
    next_free_index = 0
    structural = config.strategy in (
        "sweep_reclaim", "sfp_strict_proxy", "absorption_reversal", "balance_fakeout",
    )

    for signal_index in candidates:
        entry_index = int(signal_index) + 1
        if entry_index >= len(featured) or entry_index < next_free_index:
            continue
        is_long = bool(long_signal.iloc[signal_index])
        direction = 1.0 if is_long else -1.0
        entry = opens[entry_index]
        atr = atrs[signal_index]
        if not np.isfinite(atr) or atr <= 0:
            continue
        if structural:
            stop = (
                float(lows[max(0, signal_index - 1) : signal_index + 1].min()) - 0.10 * atr
                if is_long
                else float(highs[max(0, signal_index - 1) : signal_index + 1].max()) + 0.10 * atr
            )
        else:
            stop = entry - config.atr_stop_multiplier * atr if is_long else entry + config.atr_stop_multiplier * atr
        risk = direction * (entry - stop)
        if risk <= 0:
            continue
        target = entry + direction * config.reward_risk * risk
        final_index = min(entry_index + config.max_hold_bars - 1, len(featured) - 1)
        exit_index = final_index
        exit_price = closes[final_index]
        exit_reason = "time"

        for bar_index in range(entry_index, final_index + 1):
            if is_long:
                stop_hit = lows[bar_index] <= stop
                target_hit = highs[bar_index] >= target
            else:
                stop_hit = highs[bar_index] >= stop
                target_hit = lows[bar_index] <= target
            if stop_hit and target_hit:
                exit_price = stop if config.same_bar_policy == "stop_first" else target
                exit_reason = "same_bar_stop_first" if config.same_bar_policy == "stop_first" else "same_bar_target_first"
                exit_index = bar_index
                break
            if stop_hit:
                exit_price, exit_reason, exit_index = stop, "stop", bar_index
                break
            if target_hit:
                exit_price, exit_reason, exit_index = target, "target", bar_index
                break

        gross_r = direction * (exit_price - entry) / risk
        if is_long:
            mfe_r = (float(highs[entry_index : exit_index + 1].max()) - entry) / risk
            mae_r = (float(lows[entry_index : exit_index + 1].min()) - entry) / risk
        else:
            mfe_r = (entry - float(lows[entry_index : exit_index + 1].min())) / risk
            mae_r = (entry - float(highs[entry_index : exit_index + 1].max())) / risk
        round_trip_rate = config.fee_rate_per_side + config.slippage_rate_per_side
        cost_r = round_trip_rate * (entry + exit_price) / risk
        net_r = gross_r - cost_r
        result = "win" if net_r > 1e-9 else ("loss" if net_r < -1e-9 else "breakeven")
        trades.append(
            {
                "symbol": symbol.upper(),
                "strategy": config.strategy,
                "timeframe": config.timeframe,
                "btc_filter": config.btc_filter,
                "direction": "long" if is_long else "short",
                "signal_time": close_times[signal_index].isoformat(),
                "entry_time": open_times[entry_index].isoformat(),
                "exit_time": close_times[exit_index].isoformat(),
                "entry": round(entry, 8),
                "stop": round(stop, 8),
                "target": round(target, 8),
                "exit": round(exit_price, 8),
                "gross_r": round(gross_r, 6),
                "cost_r": round(cost_r, 6),
                "net_r": round(net_r, 6),
                "mfe_r": round(mfe_r, 6),
                "mae_r": round(mae_r, 6),
                "result": result,
                "exit_reason": exit_reason,
                "bars_held": int(exit_index - entry_index + 1),
                "volume_ratio_at_signal": round(volume_ratio_values[signal_index], 4),
                "delta_at_signal": round(delta_values[signal_index], 4),
            }
        )
        next_free_index = exit_index + 1

    split_index = max(1, min(len(featured) - 1, int(len(featured) * config.train_fraction)))
    split_time = featured.iloc[split_index]["open_time"] if len(featured) > 1 else featured.iloc[0]["open_time"]
    train = [t for t in trades if pd.Timestamp(t["entry_time"]) < split_time]
    test = [t for t in trades if pd.Timestamp(t["entry_time"]) >= split_time]
    return {
        "symbol": symbol.upper(),
        "config": asdict(config),
        "data": {
            "bars": len(featured),
            "from": featured.iloc[0]["open_time"].isoformat() if len(featured) else None,
            "to": featured.iloc[-1]["close_time"].isoformat() if len(featured) else None,
            "split_time": split_time.isoformat(),
        },
        "metrics": _trade_metrics(trades),
        "train_metrics": _trade_metrics(train),
        "test_metrics": _trade_metrics(test),
        "long_metrics": _trade_metrics([t for t in trades if t["direction"] == "long"]),
        "short_metrics": _trade_metrics([t for t in trades if t["direction"] == "short"]),
        "trades": trades,
    }


def run_suite(
    symbols: Iterable[str],
    timeframes: Iterable[str],
    reward_risks: Iterable[float],
    volume_ratios: Iterable[float],
    strategies: Iterable[str] = STRATEGIES,
    start_ms: int | None = None,
    end_ms: int | None = None,
    include_btc_filter: bool = True,
    max_hold_bars: int = 96,
    sides: Iterable[str] = ("both",),
) -> dict[str, Any]:
    """Run a transparent parameter grid and rank primarily by out-of-sample expectancy."""
    symbols = [s.upper() for s in symbols]
    timeframes = list(timeframes)
    required_symbols = set(symbols)
    if include_btc_filter:
        required_symbols.add("BTCUSDT")
    raw: dict[str, pd.DataFrame] = {
        symbol: load_history(symbol, "1m", start_ms, end_ms) for symbol in required_symbols
    }
    results: list[dict[str, Any]] = []
    all_trades: list[dict[str, Any]] = []
    for timeframe in timeframes:
        frames = {symbol: resample_klines(frame, timeframe) for symbol, frame in raw.items()}
        btc_featured = add_features(frames["BTCUSDT"], volume_ratio=1.0, poi_lookback=20) if include_btc_filter else None
        for symbol in symbols:
            filter_options = [False, True] if include_btc_filter and symbol != "BTCUSDT" else [False]
            for ratio in volume_ratios:
                featured = add_features(frames[symbol], float(ratio), poi_lookback=30)
                for strategy in strategies:
                    base_signals = _strategy_signals(featured, strategy)
                    for btc_filter in filter_options:
                        signals = (
                            _apply_btc_filter(featured, btc_featured, *base_signals)
                            if btc_filter and btc_featured is not None
                            else base_signals
                        )
                        for rr in reward_risks:
                            for side in sides:
                                cfg = BacktestConfig(
                                    strategy=strategy,
                                    timeframe=timeframe,
                                    reward_risk=float(rr),
                                    volume_ratio=float(ratio),
                                    max_hold_bars=max_hold_bars,
                                    btc_filter=btc_filter,
                                    side=side,
                                )
                                result = run_backtest(
                                    symbol,
                                    frames[symbol],
                                    cfg,
                                    btc_featured if btc_filter else None,
                                    _featured=featured,
                                    _signals=signals,
                                )
                                summary = {k: v for k, v in result.items() if k != "trades"}
                                summary["run_id"] = len(results) + 1
                                results.append(summary)
                                for trade in result["trades"]:
                                    trade["run_id"] = summary["run_id"]
                                all_trades.extend(result["trades"])

    def rank_key(item: dict[str, Any]) -> tuple[int, int, float, int, float]:
        test = item["test_metrics"]
        train = item["train_metrics"]
        expectancy = test["expectancy_r"] if test["expectancy_r"] is not None else -999.0
        eligible = int(test["trades"] >= 30)
        stable = int(
            train["expectancy_r"] is not None
            and test["expectancy_r"] is not None
            and train["expectancy_r"] > 0
            and test["expectancy_r"] > 0
        )
        return eligible, stable, float(expectancy), int(test["trades"]), float(item["metrics"]["expectancy_r"] or -999.0)

    ranked = sorted(results, key=rank_key, reverse=True)
    for rank, result in enumerate(ranked, start=1):
        result["rank"] = rank
    try:
        commit_process = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=resolve_path("."),
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        commit = commit_process.stdout.strip() or None
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=resolve_path("."),
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            ).stdout.strip()
        )
    except (OSError, subprocess.SubprocessError):
        commit = None
        dirty = None
    coverage = {}
    for symbol in required_symbols:
        frame = raw[symbol]
        frozen_start = start_ms if start_ms is not None else int(frame.iloc[0]["open_time"].timestamp() * 1000)
        frozen_end = end_ms if end_ms is not None else int(frame.iloc[-1]["open_time"].timestamp() * 1000)
        coverage[symbol] = historical.coverage_report(symbol, "1m", frozen_start, frozen_end).to_dict()
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "code_commit": commit,
        "code_dirty": dirty,
        "data_coverage": coverage,
        "methodology": {
            "entry": "next candle open after a closed-bar signal",
            "same_bar_policy": "stop_first (conservative)",
            "costs": "5 bps fee + 2 bps slippage per side",
            "funding": "not modeled in the kline-only suite",
            "poi": "rolling prior support/resistance proxy; not the user's historical manual POIs",
            "unavailable_from_klines": "footprint POC, stacked imbalance, finished auction, passive absorption and historical DOM",
            "selection_warning": "Rank is exploratory; prefer >=30 out-of-sample trades and stable train/test expectancy.",
        },
        "results": ranked,
        "trades": all_trades,
    }


def save_suite(suite: dict[str, Any], output_dir: str | Path | None = None) -> dict[str, str]:
    """Write machine-readable summary plus ATAS-friendly entry/exit timestamps."""
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = resolve_path(f"reports/backtests/{stamp}")
    else:
        output = Path(output_dir)
        if not output.is_absolute():
            output = resolve_path(str(output))
    output.mkdir(parents=True, exist_ok=True)
    summary_path = output / "summary.json"
    trades_path = output / "trades.csv"
    ranking_path = output / "ranking.csv"
    summary_without_trades = {k: v for k, v in suite.items() if k != "trades"}
    summary_path.write_text(json.dumps(summary_without_trades, indent=2, ensure_ascii=False), encoding="utf-8")
    pd.DataFrame(suite["trades"]).to_csv(trades_path, index=False)
    flat_rows = []
    for result in suite["results"]:
        cfg = result["config"]
        flat_rows.append(
            {
                "rank": result["rank"],
                "run_id": result["run_id"],
                "symbol": result["symbol"],
                "strategy": cfg["strategy"],
                "timeframe": cfg["timeframe"],
                "rr": cfg["reward_risk"],
                "volume_ratio": cfg["volume_ratio"],
                "btc_filter": cfg["btc_filter"],
                "side": cfg["side"],
                **{f"full_{k}": v for k, v in result["metrics"].items()},
                **{f"test_{k}": v for k, v in result["test_metrics"].items()},
            }
        )
    pd.DataFrame(flat_rows).to_csv(ranking_path, index=False)
    return {"directory": str(output), "summary": str(summary_path), "ranking": str(ranking_path), "trades": str(trades_path)}
