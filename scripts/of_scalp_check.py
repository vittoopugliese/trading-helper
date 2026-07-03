"""Quick scalp order flow check for HYPE."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import market_data
from agent.metrics import (
    compute_delta_series,
    compute_real_order_flow,
    compute_derivatives,
    compute_indicators,
    compute_structure,
)

SYMBOL = "HYPEUSDT"
print(f"ticker: {market_data.fetch_ticker_price(SYMBOL)}\n")

for tf in ["1m", "5m", "15m", "30m", "1h"]:
    market_data.update_cache(SYMBOL, tf, limit=150)
    df = market_data.load_klines_df(SYMBOL, tf, limit=150)
    delta = compute_delta_series(df)
    df["delta"] = delta
    df["cvd"] = delta.cumsum()
    price = float(df["close"].iloc[-1])
    last8 = df.tail(8)
    cvd_slope = float(df["cvd"].iloc[-1] - df["cvd"].iloc[-8])
    delta_sum6 = float(last8["delta"].sum())
    price_chg6 = price - float(df["close"].iloc[-8])
    ind = compute_indicators(df)
    struct = compute_structure(df)
    rsi = ind.get("rsi")
    rsi_s = f"{rsi:.1f}" if rsi else "n/a"
    print(f"=== {tf} === price={price:.3f}")
    print(f"  CVD slope 8v: {cvd_slope:+.0f} | delta sum 8v: {delta_sum6:+.0f} | price chg 8v: {price_chg6:+.3f}")
    print(f"  RSI={rsi_s} vol_ratio={ind.get('volume_ratio')} trend={struct.get('trend')}")
    print(f"  last swing hi={struct.get('last_swing_high')} lo={struct.get('last_swing_low')}")
    print("  last 8 candles:")
    for _, r in last8.iterrows():
        print(f"    {str(r['open_time'])[11:16]} C={r['close']:.2f} d={r['delta']:+.0f} v={r['volume']:.0f}")
    print()

of = compute_real_order_flow(SYMBOL)
print("=== LIVE ===")
for k in ["bias", "buy_ratio", "recent_delta_sum", "cvd_slope_5", "cvd_divergence", "book_ratio", "book_bias"]:
    if k in of:
        print(f"  {k}: {of[k]}")
d = compute_derivatives(SYMBOL)
print(f"  OI chg 1h: {d.get('oi_change_1h')} | L/S: {d.get('long_short_ratio')}")
