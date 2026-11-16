"""Deterministic market metrics: order flow, indicators, structure."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    atas: dict[str, Any] = field(default_factory=dict)
    liquidations: dict[str, Any] = field(default_factory=dict)
    acquired_at: str = ""  # when the evidence was fetched (timestamp = candle open time)

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
            "atas": self.atas,
            "liquidations": self.liquidations,
            "acquired_at": self.acquired_at,
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

    result: dict[str, Any] = {"source": "aggTrades", "window": f"ultimos {lookback} aggTrades"}

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

    times = [t.get("time") for t in trades if t.get("time") is not None]
    # Names carry their unit: these windows are trades, not candles (they used to overwrite
    # the candle fields with the same name, so "_5" stopped meaning five candles).
    result.update(
        {
            "trades_last_delta": round(last_delta, 4),
            "recent_delta_sum": round(recent_sum, 4),
            "trades_cvd": round(last_cvd, 4),
            "trades_cvd_slope_5trades": round(cvd_slope, 4),
            "bias": bias,
            "buy_ratio": buy_ratio,
            "trade_count": len(trades),
            "trades_window_sec": round((max(times) - min(times)) / 1000, 1) if len(times) >= 2 else None,
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

    # Only an aggressor flow on our side confirms (signal 4 of the gatillo). The rest are
    # labelled *_proxy / *_candidate: this function sees aggregates, not the candle sequence,
    # the wick or the footprint, so it cannot claim a sweep or an absorption happened.
    # The real footprint confirmation comes from ATAS (setup_engine atas_footprint).
    if direction == "long":
        if bias == "bullish" or buy_ratio > 0.55:
            patterns.append("delta_confirmation")
            confirmed = True
        if book_bias == "bullish":
            patterns.append("book_support")
        if divergence == "bullish":
            patterns.append("cvd_bullish_divergence")
        if in_poi and bias == "mixed" and buy_ratio >= absorption_ratio:
            patterns.append("absorption_proxy")
        if in_poi and vol_spike:
            patterns.append("sweep_candidate")
    elif direction == "short":
        if bias == "bearish" or buy_ratio < 0.45:
            patterns.append("delta_confirmation")
            confirmed = True
        if book_bias == "bearish":
            patterns.append("book_resistance")
        if divergence == "bearish":
            patterns.append("cvd_bearish_divergence")
        if in_poi and bias == "mixed" and buy_ratio <= (1 - absorption_ratio):
            patterns.append("delta_drain_proxy")
        if in_poi and vol_spike:
            patterns.append("sweep_candidate")

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
    """Candle fields keep their meaning; aggTrades adds its own fields and the bias.

    If aggTrades failed, the error is kept (`trades_error`) and the bias stays from candles.
    """
    if "error" in real_of:
        return {**kline_of, "trades_error": real_of["error"], "bias_source": "klines"}
    merged = {**kline_of, **{k: v for k, v in real_of.items() if k != "source"}}
    merged["kline_bias"] = kline_of.get("bias")
    merged["bias_source"] = "aggTrades"
    merged["source"] = "klines+aggTrades"
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

    # Context levels are used only when the scenario for THIS direction is coherent (SL and TP
    # on the right side of the POI), fresh in price (POI within context_stale_pct) and in time
    # (edited within context_max_age_days). Otherwise ATR/structure levels, labelled why.
    scenario = trade_context.scenario(direction) if trade_context and direction in ("long", "short") else None
    ctx_drift = None
    fallback_reason = None
    if trade_context and trade_context.has_poi() and scenario is None:
        fallback_reason = f"sin escenario {direction} coherente en el contexto"
    if scenario:
        lo, hi = scenario["poi_low"], scenario["poi_high"]
        ctx_drift = 0.0 if lo <= price <= hi else (lo - price if price < lo else price - hi) / price
        if ctx_drift > float(cfg["thresholds"].get("context_stale_pct", 0.08)):
            fallback_reason = "contexto desactualizado"
        elif trade_context.is_expired():
            fallback_reason = f"contexto vencido ({trade_context.age_days():.0f} dias)"
    ctx_stale = fallback_reason == "contexto desactualizado"

    if scenario and fallback_reason is None:
        entry = (scenario["poi_low"] + scenario["poi_high"]) / 2
        stop = scenario["invalidation"]
        target = scenario["targets"][0]
        rr = round(abs(target - entry) / abs(entry - stop), 2) if entry != stop else 0.0
        return {
            "entry": round(float(entry), 6),
            "stop": round(float(stop), 6),
            "target": round(float(target), 6),
            "risk_reward": rr,
            "source": f"context ({scenario['source']})",
            "targets_all": scenario["targets"],  # type: ignore[dict-item]
        }

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

    out: dict[str, float | None | str] = {
        "entry": round(price, 6),
        "stop": round(float(stop), 6) if stop else None,
        "target": round(float(target), 6) if target else None,
        "risk_reward": rr,
        "source": f"atr ({fallback_reason})" if fallback_reason else "atr",
    }
    if ctx_stale:
        out["context_stale"] = True  # type: ignore[assignment]
        out["context_drift_pct"] = round(ctx_drift, 4) if ctx_drift is not None else None
    return out


def compute_position_size(
    entry: float,
    stop: float | None,
    account_size: float | None = None,
    risk_pct: float | None = None,
    open_trades: int | None = None,
) -> dict[str, Any]:
    """Position size under every configured cap (see agent/policy.size_position)."""
    from agent import policy

    return policy.size_position(entry, stop, account=account_size, risk_pct=risk_pct, open_trades=open_trades)


def compute_external_flow(symbol: str, timeframe: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """ATAS footprint (if the bridge is streaming) + live liquidations. Never raises."""
    from agent import atas_feed, liquidations

    try:
        atas = atas_feed.best_snapshot(symbol, timeframe)
    except Exception as exc:
        atas = {"available": False, "reason": f"error ATAS: {exc}"}
    try:
        liq = liquidations.summary(symbol)
    except Exception as exc:
        liq = {"ready": False, "error": str(exc)}
    return atas, liq


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
    snapshot.atas, snapshot.liquidations = compute_external_flow(symbol, timeframe)
    snapshot.acquired_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

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
