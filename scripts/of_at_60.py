"""Analyze HYPE at ~60 support zone."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import market_data, metrics, scalp_alerts, setup_engine
from agent.context import get_context

SYMBOL = "HYPEUSDT"
ctx = get_context(SYMBOL)
print(f"ticker: {market_data.fetch_ticker_price(SYMBOL)}\n")

for tf in ["1m", "5m", "15m"]:
    market_data.update_cache(SYMBOL, tf, limit=80)
    df = market_data.load_klines_df(SYMBOL, tf, limit=80)
    d = metrics.compute_delta_series(df)
    df["delta"] = d
    p = float(df["close"].iloc[-1])
    lo = float(df["low"].iloc[-12:].min())
    hi = float(df["high"].iloc[-12:].max())
    ind = metrics.compute_indicators(df)
    print(f"=== {tf} px={p:.3f} range12 lo={lo:.3f} hi={hi:.3f} vol={ind.get('volume_ratio')} ===")
    for _, r in df.tail(6).iterrows():
        ts = str(r["open_time"])[5:16]
        print(
            f"  {ts} L={r['low']:.2f} H={r['high']:.2f} C={r['close']:.2f} "
            f"d={r['delta']:+.0f} v={r['volume']:.0f}"
        )
    print()

of = metrics.compute_real_order_flow(SYMBOL)
print("LIVE", {k: of.get(k) for k in ["bias", "buy_ratio", "recent_delta_sum", "book_ratio", "book_bias", "cvd_divergence"]})
oi_r, oi_pct, oi_abs = scalp_alerts.recent_oi_rise(SYMBOL)
print(f"OI 20m: {'SUBE' if oi_r else 'BAJA'} {oi_pct*100:+.2f}% ({oi_abs:+.0f})\n")

long_alerts = scalp_alerts.detect_scalp_alerts(
    SYMBOL, "5m",
    long_support=(59.80, 60.50),
    long_invalidation=59.50,
    long_targets=(61.15, 62.20),
)
short_alerts = scalp_alerts.detect_scalp_alerts(
    SYMBOL, "5m",
    short_resistance=(60.80, 61.15),
    short_invalidation=61.40,
    short_targets=(59.50, 59.00),
)
print("LONG alerts:", scalp_alerts.format_alerts(long_alerts) or "none")
print("SHORT alerts:", scalp_alerts.format_alerts(short_alerts) or "none")

for tf in ["5m", "15m"]:
    snap = metrics.build_snapshot(SYMBOL, tf, "scalp", trade_context=ctx)
    v = setup_engine.evaluate_setup(snap, trade_context=ctx)
    print(f"\n{tf} grade={v.grade} dir={v.direction} score={v.score*100:.0f}% at_poi={v.at_poi} patterns={v.of_patterns}")
