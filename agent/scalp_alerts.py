"""Fast scalp trigger detection — alerts before full A+ grade."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from agent import market_data, metrics
from agent.config import get_config
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
    timeframe: str = ""
    blocked_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "timeframe": self.timeframe,
            "blocked_reasons": self.blocked_reasons,
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
    order_flow: dict[str, Any] | None = None,
) -> list[ScalpAlert]:
    """Detect scalp triggers from live order flow.

    Delta thresholds are a share of each candle's own volume (they used to be absolute
    contract counts, which mean different things on HYPE and BTC). Every alert still has to
    pass `validate_alert` (geometry + minimum R:R) before it may be urgent.
    `order_flow` lets the caller reuse an aggTrades/book read instead of fetching again.
    """
    alerts: list[ScalpAlert] = []
    df = market_data.load_klines_df(symbol, timeframe, limit=30)
    if len(df) < 8:
        return alerts

    price = float(df["close"].iloc[-1])
    delta = metrics.compute_delta_series(df)
    of = order_flow if order_flow is not None else metrics.compute_real_order_flow(symbol)
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
    v1 = float(df["volume"].iloc[-2]) or 1.0
    v2 = float(df["volume"].iloc[-3]) or 1.0
    v3 = float(df["volume"].iloc[-4]) or 1.0
    last = df.iloc[-2]
    last_range = float(last["high"] - last["low"])
    close_pos = (float(last["close"]) - float(last["low"])) / last_range if last_range > 0 else 0.5

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
        urgency = "now" if at_resistance and d1 <= -0.15 * v1 else "watch"
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
        d3 >= 0.10 * v3
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
    if short_resistance and drop_from_hi >= 0.002 and d1 <= -0.20 * v1 and buy_ratio < 0.42:
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
    # Same 1.5x as the notes / setup engine (thresholds.volume_spike_ratio), on the trigger
    # candle or the one before it (the spike usually prints on the wick candle).
    spike_ratio = float(get_config()['thresholds'].get('volume_spike_ratio', 1.5))
    vol_spike = max(last_vol, v2) >= vol_ma * spike_ratio if vol_ma else False

    # --- LONG: absorcion (gatillo de 4 señales): en soporte, venta agresiva previa (delta
    # <= -10% del volumen), flip a delta positivo, volumen sobre la media y la vela cerrada
    # con cierre en su mitad superior (rechazo del minimo). Sin las cuatro = no hay absorcion.
    absorption = (
        at_support
        and d2 <= -0.10 * v2
        and d1 > 0
        and vol_spike
        and close_pos >= 0.5
        and book_bias in ("bullish", "neutral", None)
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
                    f"d2={d2:+.0f} ({d2 / v2:+.0%} vol) d1={d1:+.0f} vol {last_vol / vol_ma:.1f}x "
                    f"cierre {close_pos:.0%} del rango buy_ratio={buy_ratio:.2f}"
                ),
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

    return sanitize_alerts(alerts)[0]


def levels_sane(direction: str, entry: float, stop: float, target: float) -> bool:
    """Stop y target del lado correcto del entry.

    Un short con el stop DEBAJO del entry (o un long con el stop arriba) no es un stop:
    suele venir de un contexto viejo y el riesgo calculado sobre esa distancia es basura.
    """
    if direction == "long":
        return stop < entry < target
    if direction == "short":
        return target < entry < stop
    return False


def validate_alert(alert: ScalpAlert, required_rr: float | None = None) -> list[str]:
    """Same level/R:R rule as the setup engine (agent/policy). Empty = may be urgent."""
    from agent import policy

    return policy.level_problems(alert.direction, alert.entry, alert.stop, alert.target, required_rr)


def sanitize_alerts(alerts: list[ScalpAlert]) -> tuple[list[ScalpAlert], list[ScalpAlert]]:
    """Separa alertas con niveles coherentes de las que hay que descartar."""
    ok: list[ScalpAlert] = []
    dropped: list[ScalpAlert] = []
    for a in alerts:
        (ok if levels_sane(a.direction, a.entry, a.stop, a.target) else dropped).append(a)
    return ok, dropped


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
