"""Historical order flow around specific timestamps (local UTC-3)."""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from agent import market_data

SYMBOL = "HYPEUSDT"
LOCAL = timezone(timedelta(hours=-3))


def to_ms(local_dt: datetime) -> int:
    return int(local_dt.replace(tzinfo=LOCAL).astimezone(timezone.utc).timestamp() * 1000)


def window(symbol: str, interval: str, center_local: datetime, before_min: int, after_min: int):
    start = to_ms(center_local - timedelta(minutes=before_min))
    rows = market_data.fetch_klines(symbol, interval, limit=200, start_time=start)
    df = pd.DataFrame(rows, columns=[
        "open_time", "open", "high", "low", "close", "volume", "close_time",
        "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore"])
    for c in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[c] = df[c].astype(float)
    df["delta"] = df["taker_buy_base"] * 2 - df["volume"]
    df["cvd"] = df["delta"].cumsum()
    end = to_ms(center_local + timedelta(minutes=after_min))
    df = df[(df["open_time"] >= to_ms(center_local - timedelta(minutes=before_min))) & (df["open_time"] <= end)]
    return df


def show(title: str, center_local: datetime, interval: str, before: int, after: int):
    print(f"\n===== {title}  [{interval}] (centro local {center_local.strftime('%m-%d %H:%M')} UTC-3) =====")
    df = window(SYMBOL, interval, center_local, before, after)
    vol_ma = df["volume"].rolling(10).mean()
    for i, (_, r) in enumerate(df.iterrows()):
        ut = datetime.fromtimestamp(r["open_time"] / 1000, tz=timezone.utc)
        lt = ut.astimezone(LOCAL)
        vma = vol_ma.iloc[i] if i < len(vol_ma) else None
        spike = ""
        if vma and vma > 0 and r["volume"] >= 1.5 * vma:
            spike = " <<VOLSPIKE"
        print(
            f"  {lt.strftime('%m-%d %H:%M')}L  O={r['open']:.2f} H={r['high']:.2f} "
            f"L={r['low']:.2f} C={r['close']:.2f} v={r['volume']:.0f} d={r['delta']:+.0f}{spike}"
        )


# Jun 24 13:45 local
show("BOUNCE Jun24 13:45", datetime(2026, 6, 24, 13, 45), "5m", 30, 60)
show("BOUNCE Jun24 13:45 (1m detail)", datetime(2026, 6, 24, 13, 45), "1m", 10, 25)

# Jun 25 10:55-11:00 local
show("Jun25 10:55-11:00", datetime(2026, 6, 25, 10, 55), "5m", 40, 40)
show("Jun25 10:55-11:00 (1m detail)", datetime(2026, 6, 25, 10, 57), "1m", 15, 20)
