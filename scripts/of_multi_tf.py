"""Multi-timeframe order flow snapshot for HYPE (1h, 4h, 30m, 15m).

Delta/CVD derived from Binance taker_buy_base per candle (real signed volume),
plus live aggTrades flow and derivatives. One-off analysis helper.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from agent import market_data
from agent.metrics import compute_delta_series, compute_real_order_flow, compute_derivatives

SYMBOL = "HYPEUSDT"
TFS = ["4h", "1h", "30m", "15m"]


def tf_report(symbol: str, tf: str, n: int = 250) -> dict:
    df = market_data.load_klines_df(symbol, tf, limit=n)
    df = df.copy()
    delta = compute_delta_series(df)
    df["delta"] = delta
    df["cvd"] = delta.cumsum()

    price = float(df["close"].iloc[-1])
    last_n = 10
    recent = df.tail(last_n)

    # CVD slope over recent window
    cvd_now = float(df["cvd"].iloc[-1])
    cvd_prev = float(df["cvd"].iloc[-last_n])
    cvd_slope = cvd_now - cvd_prev

    # delta sum recent
    delta_sum_recent = float(recent["delta"].sum())

    # volume context
    vol_ma20 = float(df["volume"].iloc[:-1].rolling(20).mean().iloc[-1])
    last_vol = float(df["volume"].iloc[-1])
    vol_ratio = last_vol / vol_ma20 if vol_ma20 else 0

    # price trend over window vs cvd -> divergence
    price_chg = price - float(df["close"].iloc[-last_n])
    divergence = None
    if price_chg < 0 and cvd_slope > 0:
        divergence = "bullish (precio baja, CVD sube)"
    elif price_chg > 0 and cvd_slope < 0:
        divergence = "bearish (precio sube, CVD baja)"

    # per-candle recent print
    rows = []
    for _, r in recent.iterrows():
        rows.append(
            f"  {str(r['open_time'])[:16]}  C={r['close']:.2f}  "
            f"vol={r['volume']:.0f}  delta={r['delta']:+.0f}"
        )

    # last swing high/low in window
    win = df.tail(60)
    swing_hi = float(win["high"].max())
    swing_lo = float(win["low"].min())

    return {
        "tf": tf,
        "price": price,
        "cvd_now": cvd_now,
        "cvd_slope": cvd_slope,
        "delta_sum_recent": delta_sum_recent,
        "vol_ratio": vol_ratio,
        "price_chg": price_chg,
        "divergence": divergence,
        "swing_hi": swing_hi,
        "swing_lo": swing_lo,
        "rows": rows,
    }


def main() -> None:
    print(f"=== ORDER FLOW MULTI-TF {SYMBOL} ===\n")

    for tf in TFS:
        try:
            rep = tf_report(SYMBOL, tf)
        except Exception as exc:
            print(f"[{tf}] error: {exc}\n")
            continue
        print(f"----- {tf.upper()} -----")
        print(f" precio: {rep['price']:.2f}")
        print(f" CVD acumulado: {rep['cvd_now']:+.0f}")
        print(f" CVD slope (10 velas): {rep['cvd_slope']:+.0f}")
        print(f" delta sum (10 velas): {rep['delta_sum_recent']:+.0f}")
        print(f" precio chg (10 velas): {rep['price_chg']:+.2f}")
        print(f" vol ratio (vs MA20): {rep['vol_ratio']:.2f}")
        print(f" rango 60v:  low {rep['swing_lo']:.2f} / high {rep['swing_hi']:.2f}")
        if rep["divergence"]:
            print(f" DIVERGENCIA: {rep['divergence']}")
        print(" ultimas 10 velas (delta por vela):")
        for line in rep["rows"]:
            print(line)
        print()

    print("----- LIVE aggTrades (flujo inmediato) -----")
    try:
        of = compute_real_order_flow(SYMBOL)
        for k in [
            "bias", "buy_ratio", "last_delta", "recent_delta_sum",
            "cvd", "cvd_slope_5", "cvd_divergence", "book_ratio", "book_bias",
            "trade_count",
        ]:
            if k in of:
                print(f"  {k}: {of[k]}")
    except Exception as exc:
        print(f"  error: {exc}")
    print()

    print("----- DERIVADOS -----")
    try:
        d = compute_derivatives(SYMBOL)
        for k, v in d.items():
            print(f"  {k}: {v}")
    except Exception as exc:
        print(f"  error: {exc}")


if __name__ == "__main__":
    main()
