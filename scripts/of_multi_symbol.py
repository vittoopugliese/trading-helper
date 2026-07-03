"""Live order flow for multiple symbols (BTC + HYPE)."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import market_data, metrics

SYMBOLS = sys.argv[1:] or ["BTCUSDT", "HYPEUSDT"]

for SYMBOL in SYMBOLS:
    print(f"\n############ {SYMBOL} ############")
    print(f"ticker LIVE: {market_data.fetch_ticker_price(SYMBOL)}")
    for tf in ["4h", "1h", "15m", "5m"]:
        market_data.update_cache(SYMBOL, tf, limit=150)
        df = market_data.load_klines_df(SYMBOL, tf, limit=150)
        d = metrics.compute_delta_series(df)
        df["delta"] = d
        df["cvd"] = d.cumsum()
        price = float(df["close"].iloc[-1])
        cvd8 = float(df["cvd"].iloc[-1] - df["cvd"].iloc[-8])
        d8 = float(df["delta"].tail(8).sum())
        pc8 = price - float(df["close"].iloc[-8])
        ind = metrics.compute_indicators(df)
        st = metrics.compute_structure(df)
        rsi = ind.get("rsi")
        rsi_s = f"{rsi:.0f}" if rsi else "n/a"
        print(f"  [{tf}] px={price:.2f} cvd8={cvd8:+.0f} d8={d8:+.0f} pc8={pc8:+.2f} "
              f"rsi={rsi_s} vol={ind.get('volume_ratio')} trend={st.get('trend')} "
              f"sH={st.get('last_swing_high')} sL={st.get('last_swing_low')}")
    of = metrics.compute_real_order_flow(SYMBOL)
    print("  LIVE:", {k: of.get(k) for k in ["bias", "buy_ratio", "recent_delta_sum", "cvd_slope_5", "cvd_divergence", "book_ratio", "book_bias"]})
    der = metrics.compute_derivatives(SYMBOL)
    print(f"  DERIV: OI_chg_1h={der.get('oi_change_1h')} funding={der.get('funding_rate')} L/S={der.get('long_short_ratio')}")
