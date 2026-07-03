"""A+ short setup hunt for HYPE on 5m (with 15m/1h context + live OF)."""
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
print(f"ticker LIVE: {market_data.fetch_ticker_price(SYMBOL)}\n")

for tf in ["5m", "15m", "1h"]:
    market_data.update_cache(SYMBOL, tf, limit=200)
    df = market_data.load_klines_df(SYMBOL, tf, limit=200)
    delta = compute_delta_series(df)
    df["delta"] = delta
    df["cvd"] = delta.cumsum()
    price = float(df["close"].iloc[-1])
    cvd_slope = float(df["cvd"].iloc[-1] - df["cvd"].iloc[-8])
    d8 = float(df["delta"].tail(8).sum())
    pc8 = price - float(df["close"].iloc[-8])
    ind = compute_indicators(df)
    st = compute_structure(df)
    rsi = ind.get("rsi")
    rsi_s = f"{rsi:.0f}" if rsi else "n/a"
    ema20 = ind.get("ema20")
    ema50 = ind.get("ema50")
    print(f"=== {tf} === px={price:.2f} | ema20={ema20:.2f} ema50={ema50:.2f} | rsi={rsi_s} | trend={st.get('trend')}")
    print(f"  CVD8={cvd_slope:+.0f} d8={d8:+.0f} pc8={pc8:+.2f} vol_ratio={ind.get('volume_ratio')}")
    print(f"  swing hi={st.get('last_swing_high')} lo={st.get('last_swing_low')}")
    # recent swings list for resistance map
    print("  recent swings:", [(s['kind'], s['price']) for s in st.get('recent_swings', [])])
    print("  last 8 candles:")
    for _, r in df.tail(8).iterrows():
        print(f"    {str(r['open_time'])[5:16]} O={r['open']:.2f} H={r['high']:.2f} L={r['low']:.2f} C={r['close']:.2f} d={r['delta']:+.0f} v={r['volume']:.0f}")
    print()

of = compute_real_order_flow(SYMBOL)
print("=== LIVE aggTrades ===")
for k in ["bias", "buy_ratio", "recent_delta_sum", "cvd", "cvd_slope_5", "cvd_divergence", "book_ratio", "book_bias"]:
    if k in of:
        print(f"  {k}: {of[k]}")
d = compute_derivatives(SYMBOL)
print("=== DERIVS ===")
for k, v in d.items():
    print(f"  {k}: {v}")
