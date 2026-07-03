"""Fast scalp trigger detection — alerts before full A+ grade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from agent import market_data, metrics
from agent.context import TradeContext


@dataclass
class ScalpAlert:
    direction: str  # long, short
    trigger: str
    urgency: str  # now, watch
    price: float
    entry: float
    stop: float
    target: float
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "direction": self.direction,
            "trigger": self.trigger,
            "urgency": self.urgency,
            "price": self.price,
            "entry": self.entry,
            "stop": self.stop,
            "target": self.target,
            "detail": self.detail,
        }


def _local_high_low(df: pd.DataFrame, lookback: int = 12) -> tuple[float, float, int]:
    window = df.tail(lookback)
    hi = float(window["high"].max())
    lo = float(window["low"].min())
    hi_idx = int(window["high"].idxmax())
    return hi, lo, hi_idx


def _ask_bid_wall_ratio(symbol: str) -> tuple[float | None, float | None, float | None]:
    try:
        depth = market_data.fetch_depth(symbol, limit=20)
        bid_vol = sum(q for _, q in depth["bids"])
        ask_vol = sum(q for _, q in depth["asks"])
        ratio = ask_vol / bid_vol if bid_vol > 0 else None
        top_ask = depth["asks"][0][0] if depth["asks"] else None
        return ratio, top_ask, ask_vol
    except Exception:
        return None, None, None


def recent_oi_rise(symbol: str, period: str = "5m", lookback: int = 4) -> tuple[bool, float, float]:
    """Short-term Open Interest change. Rising OI = new positions opening.

    Returns (is_rising, oi_delta_pct, oi_delta_abs). oi_delta_pct as decimal.
    """
    try:
        hist = market_data.fetch_open_interest_hist(symbol, period=period, limit=lookback + 1)
        if len(hist) < 2:
            return False, 0.0, 0.0
        oi_now = hist[-1]["open_interest"]
        oi_then = hist[0]["open_interest"]
        delta_abs = oi_now - oi_then
        delta_pct = delta_abs / oi_then if oi_then else 0.0
        return delta_abs > 0, round(delta_pct, 4), round(delta_abs, 2)
    except Exception:
        return False, 0.0, 0.0


def detect_scalp_alerts(
    symbol: str,
    timeframe: str = "1m",
    short_resistance: tuple[float, float] | None = (70.50, 70.90),
    short_invalidation: float = 71.05,
    short_targets: tuple[float, ...] = (70.00, 69.40),
    long_support: tuple[float, float] | None = (70.25, 70.45),
    long_invalidation: float = 70.15,
    long_targets: tuple[float, ...] = (70.80, 71.00),
) -> list[ScalpAlert]:
    """Detect actionable scalp triggers from live order flow."""
    alerts: list[ScalpAlert] = []
    df = market_data.load_klines_df(symbol, timeframe, limit=30)
    if len(df) < 8:
        return alerts

    price = float(df["close"].iloc[-1])
    delta = metrics.compute_delta_series(df)
    of = metrics.compute_real_order_flow(symbol)
    buy_ratio = of.get("buy_ratio", 0.5)
    recent_delta = of.get("recent_delta_sum", 0)
    book_bias = of.get("book_bias")
    ask_bid_ratio, top_ask, ask_vol = _ask_bid_wall_ratio(symbol)

    hi, lo, _ = _local_high_low(df, 12)
    drop_from_hi = (hi - price) / hi if hi else 0
    bounce_from_lo = (price - lo) / lo if lo else 0

    # Last 3 closed candles delta (exclude in-progress)
    d1 = float(delta.iloc[-2])
    d2 = float(delta.iloc[-3])
    d3 = float(delta.iloc[-4])
    bounce_delta = d3 + d2 + d1

    # --- SHORT: rejection at resistance after bounce ---
    at_resistance = short_resistance and short_resistance[0] <= price <= short_resistance[1]
    near_resistance = short_resistance and price >= short_resistance[0] - 0.05
    at_support = long_support and long_support[0] <= price <= long_support[1]
    near_support = long_support and price <= long_support[1] + 0.05
    rejection = (
        d1 < 0
        and buy_ratio < 0.48
        and (book_bias == "bearish" or (ask_bid_ratio and ask_bid_ratio > 1.5))
    )
    exhaustion_after_bounce = d2 > 0 and d1 < 0 and buy_ratio < 0.50

    if (at_resistance or near_resistance) and rejection:
        urgency = "now" if at_resistance and d1 < -500 else "watch"
        alerts.append(
            ScalpAlert(
                direction="short",
                trigger="rejection_at_resistance",
                urgency=urgency,
                price=price,
                entry=price,
                stop=short_invalidation,
                target=short_targets[0],
                detail=(
                    f"Rechazo en resistencia {short_resistance}. "
                    f"delta={d1:+.0f} buy_ratio={buy_ratio:.2f} "
                    f"book={book_bias} ask/bid={f'{ask_bid_ratio:.2f}' if ask_bid_ratio else 'n/a'}"
                ),
            )
        )

    # --- SHORT: delta drain at resistance (SETUP_RULE patron entrada) ---
    # Secuencia: delta positivo decreciente -> flip negativo en POI = agotamiento comprador
    delta_drain = (
        d3 > 300
        and d2 > 0
        and d1 < 0
        and d2 < d3 * 0.65
        and buy_ratio < 0.50
    )
    if delta_drain and (at_resistance or near_resistance):
        alerts.append(
            ScalpAlert(
                direction="short",
                trigger="delta_drain_at_resistance",
                urgency="now" if at_resistance else "watch",
                price=price,
                entry=price,
                stop=short_invalidation,
                target=short_targets[0],
                detail=(
                    f"Drenaje delta en POI {short_resistance}: "
                    f"d3={d3:+.0f} d2={d2:+.0f} d1={d1:+.0f} buy_ratio={buy_ratio:.2f} "
                    f"(precio sube, compra se agota)"
                ),
            )
        )

    if exhaustion_after_bounce and near_resistance:
        alerts.append(
            ScalpAlert(
                direction="short",
                trigger="delta_flip_after_bounce",
                urgency="now",
                price=price,
                entry=price,
                stop=min(hi + 0.05, short_invalidation),
                target=short_targets[0],
                detail=f"Delta flip post-rebote: d2={d2:+.0f} d1={d1:+.0f} high={hi:.3f}",
            )
        )

    # --- SHORT: breakdown already started (chase with tight stop) ---
    if short_resistance and drop_from_hi >= 0.002 and d1 < -800 and buy_ratio < 0.42:
        alerts.append(
            ScalpAlert(
                direction="short",
                trigger="breakdown_in_progress",
                urgency="watch",
                price=price,
                entry=price,
                stop=hi,
                target=short_targets[1] if len(short_targets) > 1 else short_targets[0],
                detail=f"Caida -{drop_from_hi*100:.2f}% desde {hi:.3f}, delta={d1:+.0f}",
            )
        )

    vol_ma = float(df["volume"].iloc[-22:-2].mean()) if len(df) >= 22 else float(df["volume"].mean())
    last_vol = float(df["volume"].iloc[-2]) if len(df) >= 2 else float(df["volume"].iloc[-1])
    vol_spike = last_vol >= vol_ma * 1.3 if vol_ma else False

    # --- LONG: absorcion conservadora (delta negativo en mecha + flip positivo) ---
    absorption = (
        at_support
        and d2 < -300
        and d1 > 0
        and buy_ratio > 0.55
        and book_bias in ("bullish", "neutral")
    )
    if absorption:
        alerts.append(
            ScalpAlert(
                direction="long",
                trigger="absorption_at_support",
                urgency="now",
                price=price,
                entry=price,
                stop=long_invalidation,
                target=long_targets[0],
                detail=(
                    f"Absorcion en soporte {long_support}: "
                    f"d2={d2:+.0f} d1={d1:+.0f} buy_ratio={buy_ratio:.2f} "
                    f"(sellers atrapados, buyers tomaron control)"
                ),
            )
        )

    # Legacy simple absorption (single candle)
    if at_support and d1 > 0 and buy_ratio > 0.55 and book_bias in ("bullish", "neutral") and not absorption:
        alerts.append(
            ScalpAlert(
                direction="long",
                trigger="absorption_at_support",
                urgency="now",
                price=price,
                entry=price,
                stop=long_invalidation,
                target=long_targets[0],
                detail=f"Absorcion en soporte {long_support} delta={d1:+.0f} buy_ratio={buy_ratio:.2f}",
            )
        )

    # --- LONG: sweep below support + reclaim (bottom of wave E) ---
    if long_support:
        swept = lo < long_support[0]
        reclaimed = price > long_support[0]
        delta_flip_up = d2 < 0 and d1 > 0
        if swept and reclaimed and (delta_flip_up or buy_ratio > 0.55) and vol_spike:
            alerts.append(
                ScalpAlert(
                    direction="long",
                    trigger="sweep_reclaim_bottom",
                    urgency="now",
                    price=price,
                    entry=price,
                    stop=min(lo - 0.03, long_invalidation),
                    target=long_targets[0],
                    detail=f"Sweep {lo:.3f} + reclaim sobre {long_support[0]}. d2={d2:+.0f} d1={d1:+.0f} buy_ratio={buy_ratio:.2f}",
                )
            )

    # --- TRAPPED TRADERS (Open Interest + delta + sweep/reclaim) ---
    # OI subiendo = posiciones NUEVAS entrando. Si entran en la direccion
    # equivocada y el precio gira, quedan atrapados -> combustible del impulso.
    oi_rising, oi_pct, oi_abs = recent_oi_rise(symbol)

    # Trapped SHORTS (señal LONG): barrido del soporte con venta agresiva +
    # OI subiendo (nuevos shorts) y luego el precio recupera el nivel.
    if long_support and oi_rising:
        swept_low = lo < long_support[0]
        sell_aggression = d2 < 0 or d3 < 0  # presion vendedora empujo el minimo
        reclaim = price > long_support[0]
        near_low = bounce_from_lo <= 0.004  # todavia cerca del minimo barrido
        if swept_low and sell_aggression and oi_pct >= 0.003:
            if reclaim and (d1 > 0 or buy_ratio > 0.52):
                alerts.append(
                    ScalpAlert(
                        direction="long",
                        trigger="trapped_shorts",
                        urgency="now",
                        price=price,
                        entry=price,
                        stop=min(lo - 0.03, long_invalidation),
                        target=long_targets[0],
                        detail=(
                            f"SHORTS ATRAPADOS: barrido {lo:.3f}, OI +{oi_pct*100:.2f}% "
                            f"(nuevos shorts {oi_abs:+.0f}), reclaim sobre {long_support[0]}. "
                            f"d2={d2:+.0f} d1={d1:+.0f} buy_ratio={buy_ratio:.2f} -> squeeze al alza"
                        ),
                    )
                )
            elif near_low:
                alerts.append(
                    ScalpAlert(
                        direction="long",
                        trigger="trapped_shorts_building",
                        urgency="watch",
                        price=price,
                        entry=long_support[1],
                        stop=min(lo - 0.03, long_invalidation),
                        target=long_targets[0],
                        detail=(
                            f"Shorts cargando en el minimo {lo:.3f} con OI +{oi_pct*100:.2f}%. "
                            f"Esperar reclaim de {long_support[0]} para confirmar squeeze."
                        ),
                    )
                )

    # Trapped LONGS (señal SHORT): barrido del techo con compra agresiva +
    # OI subiendo (nuevos longs) y luego el precio pierde el nivel.
    if short_resistance and oi_rising:
        swept_high = hi > short_resistance[1]
        buy_aggression = d2 > 0 or d3 > 0
        lose_level = price < short_resistance[1]
        if swept_high and buy_aggression and oi_pct >= 0.003 and lose_level and (d1 < 0 or buy_ratio < 0.48):
            alerts.append(
                ScalpAlert(
                    direction="short",
                    trigger="trapped_longs",
                    urgency="now",
                    price=price,
                    entry=price,
                    stop=max(hi + 0.03, short_invalidation),
                    target=short_targets[0],
                    detail=(
                        f"LONGS ATRAPADOS: barrido {hi:.3f}, OI +{oi_pct*100:.2f}% "
                        f"(nuevos longs {oi_abs:+.0f}), perdida de {short_resistance[1]}. "
                        f"d2={d2:+.0f} d1={d1:+.0f} buy_ratio={buy_ratio:.2f} -> liquidacion a la baja"
                    ),
                )
            )

    return alerts


def format_alerts(alerts: list[ScalpAlert]) -> str:
    if not alerts:
        return ""
    lines = []
    for a in alerts:
        flag = "*** ENTRAR ***" if a.urgency == "now" else "[WATCH]"
        lines.append(
            f"{flag} {a.direction.upper()} {a.trigger} @ {a.price:.4f} | "
            f"entry {a.entry:.4f} stop {a.stop:.4f} target {a.target:.4f}\n  {a.detail}"
        )
    return "\n".join(lines)
