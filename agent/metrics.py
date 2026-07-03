"""Deterministic market metrics: order flow, indicators, structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import EMAIndicator
from ta.volatility import AverageTrueRange

from agent.config import get_config
from agent import market_data
from agent.context import TradeContext


@dataclass
class SwingPoint:
    index: int
    price: float
    kind: str  # "high" or "low"
    time: str


@dataclass
class MarketSnapshot:
    symbol: str
    timeframe: str
    trade_type: str
    price: float
    timestamp: str
    indicators: dict[str, Any] = field(default_factory=dict)
    order_flow: dict[str, Any] = field(default_factory=dict)
    structure: dict[str, Any] = field(default_factory=dict)
    derivatives: dict[str, Any] = field(default_factory=dict)
    context_timeframes: dict[str, dict[str, Any]] = field(default_factory=dict)
    trade_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trade_type": self.trade_type,
            "price": self.price,
            "timestamp": self.timestamp,
            "indicators": self.indicators,
            "order_flow": self.order_flow,
            "structure": self.structure,
            "derivatives": self.derivatives,
            "context_timeframes": self.context_timeframes,
            "trade_context": self.trade_context,
        }


def compute_delta_series(df: pd.DataFrame) -> pd.Series:
    """Delta = taker buy - taker sell per candle."""
    taker_buy = df["taker_buy_base"]
    taker_sell = df["volume"] - taker_buy
    return taker_buy - taker_sell


def compute_cvd(df: pd.DataFrame) -> pd.Series:
    """Cumulative volume delta."""
    return compute_delta_series(df).cumsum()


def compute_real_order_flow(symbol: str) -> dict[str, Any]:
    """Real delta/CVD from aggTrades + order book imbalance."""
    cfg = get_config()
    of_cfg = cfg.get("order_flow", {})
    lookback = of_cfg.get("cvd_lookback_trades", 200)
    div_lookback = of_cfg.get("divergence_lookback", 20)
    book_ratio_thresh = cfg["thresholds"].get("book_imbalance_ratio", 1.3)

    result: dict[str, Any] = {"source": "aggTrades"}

    try:
        trades = market_data.fetch_agg_trades(symbol)
    except Exception as exc:
        result["error"] = str(exc)
        return result

    if not trades:
        result["error"] = "No aggTrades returned"
        return result

    deltas = []
    for t in trades:
        signed = -t["qty"] if t["is_buyer_maker"] else t["qty"]
        deltas.append(signed)

    cvd_series = np.cumsum(deltas)
    recent = deltas[-lookback:] if len(deltas) >= lookback else deltas
    recent_sum = float(sum(recent))
    last_delta = float(deltas[-1])
    last_cvd = float(cvd_series[-1])
    cvd_slope = float(cvd_series[-1] - cvd_series[-6]) if len(cvd_series) >= 6 else 0.0

    buy_vol = sum(t["qty"] for t in trades if not t["is_buyer_maker"])
    sell_vol = sum(t["qty"] for t in trades if t["is_buyer_maker"])
    total = buy_vol + sell_vol
    buy_ratio = round(buy_vol / total, 4) if total > 0 else 0.5

    if recent_sum > 0 and cvd_slope > 0:
        bias = "bullish"
    elif recent_sum < 0 and cvd_slope < 0:
        bias = "bearish"
    else:
        bias = "mixed"

    result.update(
        {
            "last_delta": round(last_delta, 4),
            "recent_delta_sum": round(recent_sum, 4),
            "cvd": round(last_cvd, 4),
            "cvd_slope_5": round(cvd_slope, 4),
            "bias": bias,
            "buy_ratio": buy_ratio,
            "trade_count": len(trades),
        }
    )

    # CVD divergence vs price
    if len(trades) >= div_lookback:
        prices = [t["price"] for t in trades[-div_lookback:]]
        cvd_slice = cvd_series[-div_lookback:]
        price_trend = prices[-1] - prices[0]
        cvd_trend = cvd_slice[-1] - cvd_slice[0]
        divergence = None
        if price_trend > 0 and cvd_trend < 0:
            divergence = "bearish"
        elif price_trend < 0 and cvd_trend > 0:
            divergence = "bullish"
        result["cvd_divergence"] = divergence
        result["price_trend_trades"] = round(price_trend, 6)
        result["cvd_trend_trades"] = round(float(cvd_trend), 4)

    # Order book
    try:
        depth = market_data.fetch_depth(symbol)
        bid_vol = sum(q for _, q in depth["bids"])
        ask_vol = sum(q for _, q in depth["asks"])
        if ask_vol > 0:
            book_ratio = bid_vol / ask_vol
            result["book_bid_vol"] = round(bid_vol, 4)
            result["book_ask_vol"] = round(ask_vol, 4)
            result["book_ratio"] = round(book_ratio, 4)
            if book_ratio >= book_ratio_thresh:
                result["book_bias"] = "bullish"
            elif book_ratio <= 1 / book_ratio_thresh:
                result["book_bias"] = "bearish"
            else:
                result["book_bias"] = "neutral"
    except Exception as exc:
        result["book_error"] = str(exc)

    return result


def detect_of_pattern_at_poi(
    order_flow: dict[str, Any],
    direction: str,
    in_poi: bool,
) -> dict[str, Any]:
    """Detect absorption / delta drain / sweep patterns per notes."""
    cfg = get_config()
    absorption_ratio = cfg["thresholds"].get("absorption_delta_ratio", 0.6)

    patterns: list[str] = []
    confirmed = False

    bias = order_flow.get("bias", "mixed")
    buy_ratio = order_flow.get("buy_ratio", 0.5)
    book_bias = order_flow.get("book_bias")
    divergence = order_flow.get("cvd_divergence")
    vol_spike = order_flow.get("volume_spike_at_poi", False)

    if direction == "long":
        if bias == "bullish" or buy_ratio > 0.55:
            patterns.append("delta_confirmation")
            confirmed = True
        if book_bias == "bullish":
            patterns.append("book_support")
            confirmed = True
        if divergence == "bullish":
            patterns.append("cvd_bullish_divergence")
        if in_poi and bias == "mixed" and buy_ratio >= absorption_ratio:
            patterns.append("absorption")
            confirmed = True
        if in_poi and vol_spike:
            patterns.append("liquidity_sweep")
            confirmed = True
    elif direction == "short":
        if bias == "bearish" or buy_ratio < 0.45:
            patterns.append("delta_confirmation")
            confirmed = True
        if book_bias == "bearish":
            patterns.append("book_resistance")
            confirmed = True
        if divergence == "bearish":
            patterns.append("cvd_bearish_divergence")
        if in_poi and bias == "mixed" and buy_ratio <= (1 - absorption_ratio):
            patterns.append("delta_drain")
            confirmed = True
        if in_poi and vol_spike:
            patterns.append("liquidity_sweep")
            confirmed = True

    return {
        "patterns": patterns,
        "confirmed": confirmed,
        "in_poi": in_poi,
    }


def find_swing_points(df: pd.DataFrame, lookback: int | None = None) -> list[SwingPoint]:
    """Detect swing highs and lows using local extrema."""
    cfg = get_config()
    lookback = lookback or cfg["thresholds"]["swing_lookback"]
    swings: list[SwingPoint] = []

    if len(df) < lookback * 2 + 1:
        return swings

    highs = df["high"].values
    lows = df["low"].values

    for i in range(lookback, len(df) - lookback):
        window_high = highs[i - lookback : i + lookback + 1]
        window_low = lows[i - lookback : i + lookback + 1]

        if highs[i] == np.max(window_high):
            swings.append(
                SwingPoint(
                    index=i,
                    price=float(highs[i]),
                    kind="high",
                    time=str(df.iloc[i]["open_time"]),
                )
            )
        if lows[i] == np.min(window_low):
            swings.append(
                SwingPoint(
                    index=i,
                    price=float(lows[i]),
                    kind="low",
                    time=str(df.iloc[i]["open_time"]),
                )
            )

    swings.sort(key=lambda s: s.index)
    return swings


def infer_trend(swings: list[SwingPoint]) -> str:
    """Simple trend from last swing highs/lows."""
    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]

    if len(highs) < 2 or len(lows) < 2:
        return "neutral"

    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price

    if hh and hl:
        return "bullish"
    if lh and ll:
        return "bearish"
    return "neutral"


def compute_indicators(df: pd.DataFrame) -> dict[str, Any]:
    """RSI, EMA, ATR and volume metrics."""
    cfg = get_config()
    close = df["close"]

    rsi = RSIIndicator(close=close, window=14).rsi()
    ema20 = EMAIndicator(close=close, window=20).ema_indicator()
    ema50 = EMAIndicator(close=close, window=50).ema_indicator()
    atr = AverageTrueRange(high=df["high"], low=df["low"], close=close, window=14).average_true_range()

    vol = df["volume"]
    if len(vol) >= 2:
        last_closed_vol = float(vol.iloc[-2])
        vol_ma20 = vol.iloc[:-1].rolling(20).mean()
        ma_ref = vol_ma20.iloc[-1]
    else:
        last_closed_vol = float(vol.iloc[-1])
        ma_ref = vol.rolling(20).mean().iloc[-1]
    vol_ratio = float(last_closed_vol / ma_ref) if ma_ref and ma_ref > 0 else 1.0

    return {
        "rsi": float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None,
        "ema20": float(ema20.iloc[-1]) if not pd.isna(ema20.iloc[-1]) else None,
        "ema50": float(ema50.iloc[-1]) if not pd.isna(ema50.iloc[-1]) else None,
        "atr": float(atr.iloc[-1]) if not pd.isna(atr.iloc[-1]) else None,
        "volume": round(last_closed_vol, 2),
        "volume_ma20": round(float(ma_ref), 2) if ma_ref and not pd.isna(ma_ref) else None,
        "volume_ratio": round(vol_ratio, 2),
        "volume_spike": vol_ratio >= cfg["thresholds"]["volume_spike_ratio"],
    }


def compute_order_flow(df: pd.DataFrame) -> dict[str, Any]:
    """Delta, CVD and recent flow bias from klines (fallback)."""
    delta = compute_delta_series(df)
    cvd = compute_cvd(df)

    recent_delta = delta.tail(5)
    recent_sum = float(recent_delta.sum())
    last_delta = float(delta.iloc[-1])
    last_cvd = float(cvd.iloc[-1])
    cvd_slope = float(cvd.iloc[-1] - cvd.iloc[-6]) if len(cvd) >= 6 else 0.0

    if recent_sum > 0 and cvd_slope > 0:
        bias = "bullish"
    elif recent_sum < 0 and cvd_slope < 0:
        bias = "bearish"
    else:
        bias = "mixed"

    return {
        "last_delta": round(last_delta, 4),
        "recent_delta_sum_5": round(recent_sum, 4),
        "cvd": round(last_cvd, 4),
        "cvd_slope_5": round(cvd_slope, 4),
        "bias": bias,
        "source": "klines",
    }


def merge_order_flow(kline_of: dict[str, Any], real_of: dict[str, Any]) -> dict[str, Any]:
    """Prefer real aggTrades data; keep kline fields as fallback."""
    if "error" in real_of:
        return kline_of
    merged = {**kline_of, **real_of}
    merged["recent_delta_sum_5"] = real_of.get("recent_delta_sum", kline_of.get("recent_delta_sum_5"))
    return merged


def compute_structure(df: pd.DataFrame) -> dict[str, Any]:
    """Swing structure and trend."""
    swings = find_swing_points(df)
    trend = infer_trend(swings)
    recent = swings[-4:] if len(swings) >= 4 else swings

    return {
        "trend": trend,
        "swing_count": len(swings),
        "recent_swings": [
            {"kind": s.kind, "price": s.price, "time": s.time} for s in recent
        ],
        "last_swing_high": next((s.price for s in reversed(swings) if s.kind == "high"), None),
        "last_swing_low": next((s.price for s in reversed(swings) if s.kind == "low"), None),
    }


def compute_derivatives(symbol: str) -> dict[str, Any]:
    """OI, funding and long/short ratio."""
    try:
        oi_current = market_data.fetch_open_interest(symbol)
        oi_hist = market_data.fetch_open_interest_hist(symbol, period="1h", limit=10)
        funding = market_data.fetch_funding_rate(symbol, limit=5)
        ls_ratio = market_data.fetch_long_short_ratio(symbol, period="1h", limit=5)
    except Exception as exc:
        return {"error": str(exc)}

    oi_change = None
    if len(oi_hist) >= 2:
        oi_change = oi_hist[-1]["open_interest"] - oi_hist[-2]["open_interest"]

    latest_funding = funding[-1]["funding_rate"] if funding else 0.0
    latest_ls = ls_ratio[-1]["long_short_ratio"] if ls_ratio else 1.0

    return {
        "open_interest": oi_current.get("open_interest"),
        "oi_change_1h": round(oi_change, 2) if oi_change is not None else None,
        "funding_rate": latest_funding,
        "funding_extreme": abs(latest_funding) > 0.0005,
        "long_short_ratio": latest_ls,
        "long_account_pct": ls_ratio[-1]["long_account"] if ls_ratio else None,
    }


def suggest_levels(
    df: pd.DataFrame,
    direction: str,
    trade_context: TradeContext | None = None,
) -> dict[str, float | None | str]:
    """Suggest entry, stop, target — uses Elliott context when available."""
    cfg = get_config()
    price = float(df["close"].iloc[-1])

    if trade_context and trade_context.has_poi():
        entry = trade_context.poi_mid() or price
        stop = trade_context.invalidation
        target = trade_context.targets[0] if trade_context.targets else None

        if stop is None:
            indicators = compute_indicators(df)
            structure = compute_structure(df)
            atr = indicators.get("atr") or 0.0
            mult_stop = cfg["thresholds"]["atr_stop_multiplier"]
            if direction == "long":
                stop = structure.get("last_swing_low") or (price - atr * mult_stop)
            elif direction == "short":
                stop = structure.get("last_swing_high") or (price + atr * mult_stop)

        if target is None:
            indicators = compute_indicators(df)
            atr = indicators.get("atr") or 0.0
            mult_target = cfg["thresholds"]["atr_target_multiplier"]
            target = price + atr * mult_target if direction == "long" else price - atr * mult_target

        risk = abs(entry - stop) if stop else 0
        reward = abs(target - entry) if target else 0
        rr = round(reward / risk, 2) if risk > 0 else 0.0

        levels: dict[str, float | None | str] = {
            "entry": round(float(entry), 6),
            "stop": round(float(stop), 6) if stop else None,
            "target": round(float(target), 6) if target else None,
            "risk_reward": rr,
            "source": "context",
        }
        if trade_context.targets:
            levels["targets_all"] = trade_context.targets  # type: ignore[assignment]
        return levels

    indicators = compute_indicators(df)
    structure = compute_structure(df)
    atr = indicators.get("atr") or 0.0
    mult_stop = cfg["thresholds"]["atr_stop_multiplier"]
    mult_target = cfg["thresholds"]["atr_target_multiplier"]

    if direction == "long":
        stop = structure.get("last_swing_low") or (price - atr * mult_stop)
        target = price + atr * mult_target
    elif direction == "short":
        stop = structure.get("last_swing_high") or (price + atr * mult_stop)
        target = price - atr * mult_target
    else:
        stop = price - atr * mult_stop
        target = price + atr * mult_target

    risk = abs(price - stop) if stop else 0
    reward = abs(target - price) if target else 0
    rr = round(reward / risk, 2) if risk > 0 else 0.0

    return {
        "entry": round(price, 6),
        "stop": round(float(stop), 6) if stop else None,
        "target": round(float(target), 6) if target else None,
        "risk_reward": rr,
        "source": "atr",
    }


def compute_position_size(
    entry: float,
    stop: float | None,
    account_size: float | None = None,
    risk_pct: float | None = None,
) -> dict[str, float | None]:
    """2% risk rule position sizing."""
    cfg = get_config()
    risk_cfg = cfg.get("risk", {})
    account = account_size or risk_cfg.get("default_account_size", 10000)
    risk = risk_pct or risk_cfg.get("max_risk_pct", 2.0)

    if stop is None or entry == stop:
        return {"account_size": account, "risk_pct": risk, "risk_amount": None, "position_size": None}

    risk_amount = account * (risk / 100)
    per_unit_risk = abs(entry - stop)
    position_size = round(risk_amount / per_unit_risk, 4) if per_unit_risk > 0 else None

    return {
        "account_size": account,
        "risk_pct": risk,
        "risk_amount": round(risk_amount, 2),
        "position_size": position_size,
        "per_unit_risk": round(per_unit_risk, 6),
    }


def build_timeframe_summary(df: pd.DataFrame) -> dict[str, Any]:
    """Compact summary for a single timeframe."""
    return {
        "indicators": compute_indicators(df),
        "order_flow": compute_order_flow(df),
        "structure": compute_structure(df),
    }


def build_snapshot(
    symbol: str,
    timeframe: str,
    trade_type: str = "swing",
    include_context: bool = True,
    trade_context: TradeContext | None = None,
) -> MarketSnapshot:
    """Build full market snapshot for analysis."""
    from agent.context import get_context

    df = market_data.load_klines_df(symbol, timeframe)
    if df.empty:
        raise ValueError(f"No data for {symbol} {timeframe}")

    price = float(df["close"].iloc[-1])
    ts = str(df["open_time"].iloc[-1])

    ctx = trade_context or get_context(symbol)
    if ctx and ctx.trade_type:
        trade_type = ctx.trade_type

    kline_of = compute_order_flow(df)
    real_of = compute_real_order_flow(symbol)
    order_flow = merge_order_flow(kline_of, real_of)

    indicators = compute_indicators(df)
    if ctx and ctx.has_poi() and ctx.price_in_poi(price):
        order_flow["volume_spike_at_poi"] = indicators.get("volume_spike", False)

    snapshot = MarketSnapshot(
        symbol=symbol.upper(),
        timeframe=timeframe,
        trade_type=trade_type,
        price=price,
        timestamp=ts,
        indicators=indicators,
        order_flow=order_flow,
        structure=compute_structure(df),
        derivatives=compute_derivatives(symbol),
        trade_context=ctx.to_dict() if ctx else None,
    )

    if include_context:
        cfg = get_config()
        tfs = cfg["timeframes"]
        for label, tf in tfs.items():
            if tf == timeframe:
                continue
            try:
                ctx_df = market_data.load_klines_df(symbol, tf, limit=100)
                if not ctx_df.empty:
                    snapshot.context_timeframes[label] = build_timeframe_summary(ctx_df)
            except Exception:
                continue

    return snapshot
