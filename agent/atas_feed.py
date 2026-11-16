"""ATAS feed: stores what the TradingHelperBridge indicator streams and derives footprint signals.

The C# indicator (atas-bridge/) only collects. Everything that decides something lives
here so it can be tested: stacked imbalances, absorption at the bar extreme, intra-bar
delta reversal, delta flip between bars, big trades and DOM imbalance.

Every payload is persisted in data/atas.db, which doubles as a research dataset: the
footprint history accumulates while ATAS runs and can be joined to signal outcomes later.
"""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from agent.config import PROJECT_ROOT, get_config

_DB_LOCK = threading.Lock()

_TF_UNITS = {"M": "m", "H": "h", "D": "d", "W": "w"}
_TF_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _cfg() -> dict[str, Any]:
    defaults = {
        "imbalance_ratio": 3.0,
        "stacked_levels": 3,
        "absorption_zone_pct": 0.25,
        "absorption_volume_share": 0.35,
        "delta_reversal_share": 0.15,
        "delta_flip_share": 0.10,
        "volume_spike_ratio": 1.5,
        "lookback_bars": 20,
        "stale_after_sec": 90,
        "dom_imbalance_ratio": 1.5,
    }
    return {**defaults, **(get_config().get("atas") or {})}


def db_path() -> Path:
    return PROJECT_ROOT / "data" / "atas.db"


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    """Commit on success and always close (an open handle locks the file on Windows)."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db() -> None:
    with _DB_LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT NOT NULL, tf TEXT NOT NULL, ts TEXT NOT NULL,
                closed INTEGER NOT NULL, open REAL, high REAL, low REAL, close REAL,
                volume REAL, ask REAL, bid REAL, delta REAL, max_delta REAL, min_delta REAL,
                ticks REAL, oi REAL, max_oi REAL, min_oi REAL, vwap REAL,
                poc REAL, vah REAL, val REAL, levels TEXT, tick_size REAL,
                exchange TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY (symbol, tf, ts)
            );
            CREATE TABLE IF NOT EXISTS big_trades (
                symbol TEXT NOT NULL, ts TEXT NOT NULL, first_price REAL NOT NULL,
                last_price REAL, volume REAL, direction TEXT NOT NULL,
                PRIMARY KEY (symbol, ts, first_price, direction)
            );
            CREATE TABLE IF NOT EXISTS dom (
                symbol TEXT NOT NULL, ts TEXT NOT NULL, best_bid REAL, best_ask REAL,
                range_pct REAL, bid_volume REAL, ask_volume REAL, cum_bids REAL, cum_asks REAL,
                bid_walls TEXT, ask_walls TEXT,
                PRIMARY KEY (symbol, ts)
            );
            CREATE TABLE IF NOT EXISTS feed_status (
                symbol TEXT NOT NULL, tf TEXT NOT NULL, instrument TEXT, exchange TEXT,
                chart_type TEXT, raw_timeframe TEXT, last_seen TEXT NOT NULL,
                PRIMARY KEY (symbol, tf)
            );
            """
        )


# --------------------------------------------------------------------------- normalisation


def normalize_symbol(instrument: str | None) -> str | None:
    """'HYPEUSDT', 'HYPEUSDT.P', 'HYPE-USDT-SWAP' -> 'HYPEUSDT'."""
    if not instrument:
        return None
    clean = re.sub(r"[^A-Z0-9]", "", instrument.upper())
    match = re.match(r"([A-Z0-9]+?)(USDT|USDC|USD)", clean)
    return (match.group(1) + match.group(2)) if match else clean or None


def normalize_timeframe(raw: str | None, chart_type: str | None = None) -> str | None:
    """ATAS 'M15' / 'H1' / 'D1' -> '15m' / '1h' / '1d'. Non-time charts keep a prefixed label."""
    if not raw:
        return None
    text = str(raw).strip()
    match = re.fullmatch(r"([MHDW])(\d+)", text, flags=re.IGNORECASE)
    if match:
        return f"{int(match.group(2))}{_TF_UNITS[match.group(1).upper()]}"
    match = re.fullmatch(r"(\d+)\s*([mhdw])", text, flags=re.IGNORECASE)
    if match:
        return f"{int(match.group(1))}{match.group(2).lower()}"
    if text.lower() in ("daily", "day"):
        return "1d"
    if text.lower() in ("hourly", "hour"):
        return "1h"
    kind = (chart_type or "chart").lower()
    return f"{kind}:{text.lower()}"


def tf_seconds(tf: str) -> int | None:
    match = re.fullmatch(r"(\d+)([mhdw])", tf or "")
    return int(match.group(1)) * _TF_SECONDS[match.group(2)] if match else None


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # The bridge sends naive timestamps; crypto feeds in ATAS run on UTC.
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- ingest


class IngestError(ValueError):
    pass


def ingest(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate + upsert one bridge payload. Returns counts for the response."""
    if not isinstance(payload, dict) or payload.get("source") != "atas":
        raise IngestError("payload sin source=atas")
    symbol = normalize_symbol(payload.get("instrument"))
    if not symbol:
        raise IngestError("instrument vacio")
    tf = normalize_timeframe(payload.get("timeframe"), payload.get("chart_type"))
    if not tf:
        raise IngestError("timeframe vacio")
    tick_size = _num(payload.get("tick_size"))
    exchange = payload.get("exchange")
    now = _iso(datetime.now(timezone.utc))

    bar_rows = []
    for bar in payload.get("bars") or []:
        ts = _parse_ts(bar.get("time"))
        if ts is None:
            continue
        levels = bar.get("levels")
        bar_rows.append(
            (
                symbol, tf, _iso(ts), 1 if bar.get("closed") else 0,
                *(_num(bar.get(k)) for k in (
                    "open", "high", "low", "close", "volume", "ask", "bid", "delta", "max_delta",
                    "min_delta", "ticks", "oi", "max_oi", "min_oi", "vwap", "poc", "vah", "val",
                )),
                json.dumps(levels) if levels is not None else None, tick_size, exchange, now,
            )
        )

    trade_rows = []
    for trade in payload.get("trades") or []:
        ts = _parse_ts(trade.get("time"))
        price = _num(trade.get("first_price"))
        direction = str(trade.get("direction") or "").lower()
        if ts is None or price is None or direction not in ("buy", "sell"):
            continue
        trade_rows.append((symbol, _iso(ts), price, _num(trade.get("last_price")), _num(trade.get("volume")), direction))

    dom = payload.get("dom")
    dom_row = None
    if isinstance(dom, dict) and _parse_ts(dom.get("time")):
        dom_row = (
            symbol, _iso(_parse_ts(dom["time"])),
            *(_num(dom.get(k)) for k in ("best_bid", "best_ask", "range_pct", "bid_volume", "ask_volume", "cum_bids", "cum_asks")),
            json.dumps(dom.get("bid_walls") or []), json.dumps(dom.get("ask_walls") or []),
        )

    init_db()
    with _DB_LOCK, _connect() as conn:
        if bar_rows:
            # A closed bar is final: a late live update for the same timestamp must not reopen it.
            conn.executemany(
                """
                INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(symbol, tf, ts) DO UPDATE SET
                    closed=excluded.closed, open=excluded.open, high=excluded.high, low=excluded.low,
                    close=excluded.close, volume=excluded.volume, ask=excluded.ask, bid=excluded.bid,
                    delta=excluded.delta, max_delta=excluded.max_delta, min_delta=excluded.min_delta,
                    ticks=excluded.ticks, oi=excluded.oi, max_oi=excluded.max_oi, min_oi=excluded.min_oi,
                    vwap=excluded.vwap, poc=excluded.poc, vah=excluded.vah, val=excluded.val,
                    levels=excluded.levels, tick_size=excluded.tick_size, exchange=excluded.exchange,
                    updated_at=excluded.updated_at
                WHERE bars.closed = 0 OR excluded.closed = 1
                """,
                bar_rows,
            )
        if trade_rows:
            conn.executemany(
                """
                INSERT INTO big_trades VALUES (?,?,?,?,?,?)
                ON CONFLICT(symbol, ts, first_price, direction) DO UPDATE SET
                    last_price=excluded.last_price, volume=excluded.volume
                """,
                trade_rows,
            )
        if dom_row:
            conn.execute("INSERT OR REPLACE INTO dom VALUES (?,?,?,?,?,?,?,?,?,?,?)", dom_row)
        conn.execute(
            "INSERT OR REPLACE INTO feed_status VALUES (?,?,?,?,?,?,?)",
            (symbol, tf, payload.get("instrument"), exchange, payload.get("chart_type"), payload.get("timeframe"), now),
        )
    return {"symbol": symbol, "tf": tf, "bars": len(bar_rows), "trades": len(trade_rows), "dom": bool(dom_row)}


def prune(days_dom: int = 7) -> None:
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=days_dom))
    with _DB_LOCK, _connect() as conn:
        conn.execute("DELETE FROM dom WHERE ts < ?", (cutoff,))


# --------------------------------------------------------------------------- footprint features


def _levels(bar: dict[str, Any]) -> list[tuple[float, float, float]]:
    raw = bar.get("levels")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    out = []
    for item in raw or []:
        if isinstance(item, (list, tuple)) and len(item) >= 3:
            price, bid, ask = (_num(x) for x in item[:3])
            if price is not None:
                out.append((price, bid or 0.0, ask or 0.0))
    out.sort(key=lambda x: x[0])
    return out


def stacked_imbalances(
    levels: list[tuple[float, float, float]], ratio: float, min_stack: int
) -> list[dict[str, Any]]:
    """Diagonal imbalances (ask at p vs bid at p-1 level) stacked >= min_stack in a row.

    Buy imbalance at level i: ask[i] >= ratio * bid[i-1]. Sell imbalance at level i:
    bid[i] >= ratio * ask[i+1]. Zero on the compared side counts only if the other side traded.
    """
    n = len(levels)
    buy = [False] * n
    sell = [False] * n
    for i in range(n):
        if i > 0:
            ask, prev_bid = levels[i][2], levels[i - 1][1]
            buy[i] = ask > 0 and ask >= ratio * max(prev_bid, 0.0) and (prev_bid > 0 or ask >= ratio)
        if i < n - 1:
            bid, next_ask = levels[i][1], levels[i + 1][2]
            sell[i] = bid > 0 and bid >= ratio * max(next_ask, 0.0) and (next_ask > 0 or bid >= ratio)

    zones: list[dict[str, Any]] = []
    for side, flags in (("buy", buy), ("sell", sell)):
        run_start = None
        for i, flag in enumerate(flags + [False]):
            if flag and run_start is None:
                run_start = i
            elif not flag and run_start is not None:
                if i - run_start >= min_stack:
                    zones.append({"side": side, "low": levels[run_start][0], "high": levels[i - 1][0], "levels": i - run_start})
                run_start = None
    return zones


def bar_features(bar: dict[str, Any], prev: dict[str, Any] | None = None, avg_volume: float | None = None) -> dict[str, Any]:
    """Footprint reading of one bar. Pure function: same input, same output."""
    cfg = _cfg()
    high, low, close = _num(bar.get("high")), _num(bar.get("low")), _num(bar.get("close"))
    volume = _num(bar.get("volume")) or 0.0
    delta = _num(bar.get("delta")) or 0.0
    min_delta = _num(bar.get("min_delta")) or 0.0
    max_delta = _num(bar.get("max_delta")) or 0.0
    levels = _levels(bar)
    out: dict[str, Any] = {"ts": bar.get("ts"), "closed": bool(bar.get("closed")), "volume": volume, "delta": delta}
    if high is None or low is None or close is None:
        return out

    rng = high - low
    out["close_pos"] = round((close - low) / rng, 3) if rng > 0 else 0.5
    out["poc"] = _num(bar.get("poc"))
    if out["poc"] is not None and rng > 0:
        out["poc_pos"] = round((out["poc"] - low) / rng, 3)

    zones = stacked_imbalances(levels, cfg["imbalance_ratio"], int(cfg["stacked_levels"]))
    out["stacked_buy"] = [z for z in zones if z["side"] == "buy"]
    out["stacked_sell"] = [z for z in zones if z["side"] == "sell"]

    # Absorption: heavy trade at the bar extreme, aggressor on that side, price rejected it.
    if levels and rng > 0 and volume > 0:
        zone = cfg["absorption_zone_pct"] * rng
        low_lv = [lv for lv in levels if lv[0] <= low + zone]
        high_lv = [lv for lv in levels if lv[0] >= high - zone]
        low_vol = sum(b + a for _, b, a in low_lv)
        high_vol = sum(b + a for _, b, a in high_lv)
        low_delta = sum(a - b for _, b, a in low_lv)
        high_delta = sum(a - b for _, b, a in high_lv)
        share = cfg["absorption_volume_share"]
        out["absorption_low"] = low_vol / volume >= share and low_delta < 0 and out["close_pos"] >= 0.5
        out["absorption_high"] = high_vol / volume >= share and high_delta > 0 and out["close_pos"] <= 0.5
        # Unfinished auction: both sides traded at the extreme -> the extreme tends to be revisited.
        out["unfinished_low"] = bool(levels[0][1] > 0 and levels[0][2] > 0)
        out["unfinished_high"] = bool(levels[-1][1] > 0 and levels[-1][2] > 0)

    if volume > 0:
        rev = cfg["delta_reversal_share"] * volume
        out["delta_reversal_up"] = min_delta <= -rev and delta > 0
        out["delta_reversal_down"] = max_delta >= rev and delta < 0

    if prev is not None:
        prev_delta = _num(prev.get("delta")) or 0.0
        prev_vol = _num(prev.get("volume")) or 0.0
        flip = cfg["delta_flip_share"]
        out["delta_flip_up"] = prev_vol > 0 and volume > 0 and prev_delta <= -flip * prev_vol and delta >= flip * volume
        out["delta_flip_down"] = prev_vol > 0 and volume > 0 and prev_delta >= flip * prev_vol and delta <= -flip * volume
        prev_oi, oi = _num(prev.get("oi")), _num(bar.get("oi"))
        if prev_oi and oi:
            out["oi_change"] = round(oi - prev_oi, 4)
            out["oi_change_pct"] = round((oi - prev_oi) / prev_oi * 100, 4)

    if avg_volume:
        out["volume_x"] = round(volume / avg_volume, 2)
        out["volume_spike"] = volume >= cfg["volume_spike_ratio"] * avg_volume

    return out


def long_short_signals(features: dict[str, Any]) -> dict[str, list[str]]:
    """Name the footprint confirmations per direction (the user's 'gatillo' in data terms)."""
    long_sig, short_sig = [], []
    if features.get("absorption_low"):
        long_sig.append("absorcion_en_minimo")
    if features.get("absorption_high"):
        short_sig.append("absorcion_en_maximo")
    if features.get("delta_reversal_up"):
        long_sig.append("delta_reversal_intra_vela")
    if features.get("delta_reversal_down"):
        short_sig.append("delta_reversal_intra_vela")
    if features.get("delta_flip_up"):
        long_sig.append("delta_flip")
    if features.get("delta_flip_down"):
        short_sig.append("delta_flip")
    if features.get("stacked_buy"):
        long_sig.append("imbalance_apilado_comprador")
    if features.get("stacked_sell"):
        short_sig.append("imbalance_apilado_vendedor")
    return {"long": long_sig, "short": short_sig}


def cvd_divergence(closed: list[dict[str, Any]]) -> tuple[str | None, str | None]:
    """Compare the last 5 closed bars with the 5 before (net delta vs price extremes).

    agotamiento: price makes a new low/high but aggression fades (delta less extreme).
    absorcion (notes/smart-money): price holds a higher low while sellers hit harder
    (bullish), or a lower high while buyers lift harder (bearish).
    """
    if len(closed) < 10:
        return None, None
    a, b = closed[-10:-5], closed[-5:]
    cvd_a = sum(x["delta"] or 0 for x in a)
    cvd_b = sum(x["delta"] or 0 for x in b)
    low_a, low_b = min(x["low"] for x in a), min(x["low"] for x in b)
    high_a, high_b = max(x["high"] for x in a), max(x["high"] for x in b)
    if low_b < low_a and cvd_b > cvd_a:
        return "bullish", "agotamiento"
    if high_b > high_a and cvd_b < cvd_a:
        return "bearish", "agotamiento"
    if low_b > low_a and cvd_b < cvd_a and cvd_b < 0:
        return "bullish", "absorcion"
    if high_b < high_a and cvd_b > cvd_a and cvd_b > 0:
        return "bearish", "absorcion"
    return None, None


# --------------------------------------------------------------------------- queries


def load_bars(symbol: str, tf: str, limit: int = 60) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM bars WHERE symbol=? AND tf=? ORDER BY ts DESC LIMIT ?",
            (symbol.upper(), tf, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def feeds() -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM feed_status ORDER BY last_seen DESC").fetchall()]


def _age_sec(ts: str | None) -> float | None:
    parsed = _parse_ts(ts)
    return (datetime.now(timezone.utc) - parsed).total_seconds() if parsed else None


def recent_big_trades(symbol: str, minutes: int = 15) -> dict[str, Any]:
    init_db()
    since = _iso(datetime.now(timezone.utc) - timedelta(minutes=minutes))
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM big_trades WHERE symbol=? AND ts>=? ORDER BY ts", (symbol.upper(), since)
        ).fetchall()]
    buy = sum(r["volume"] or 0 for r in rows if r["direction"] == "buy")
    sell = sum(r["volume"] or 0 for r in rows if r["direction"] == "sell")
    return {"minutes": minutes, "count": len(rows), "buy_volume": buy, "sell_volume": sell,
            "net": buy - sell, "last": rows[-5:]}


def latest_dom(symbol: str, max_age_sec: int = 60) -> dict[str, Any] | None:
    init_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM dom WHERE symbol=? ORDER BY ts DESC LIMIT 1", (symbol.upper(),)).fetchone()
    if not row:
        return None
    out = dict(row)
    age = _age_sec(out["ts"])
    if age is None or age > max_age_sec:
        return None
    out["bid_walls"] = json.loads(out["bid_walls"] or "[]")
    out["ask_walls"] = json.loads(out["ask_walls"] or "[]")
    bid, ask = out.get("bid_volume") or 0.0, out.get("ask_volume") or 0.0
    ratio = bid / ask if ask else None
    out["ratio"] = round(ratio, 3) if ratio else None
    thr = _cfg()["dom_imbalance_ratio"]
    out["bias"] = "bullish" if ratio and ratio >= thr else "bearish" if ratio and ratio <= 1 / thr else "neutral"
    return out


def snapshot(symbol: str, tf: str) -> dict[str, Any]:
    """Everything ATAS knows about symbol/tf right now. `available` False = do not use."""
    cfg = _cfg()
    bars = load_bars(symbol, tf, limit=int(cfg["lookback_bars"]) + 30)
    if not bars:
        return {"available": False, "reason": f"sin datos ATAS para {symbol} {tf}"}

    last_update_age = _age_sec(max(b["updated_at"] for b in bars))
    tf_sec = tf_seconds(tf) or 60
    fresh = last_update_age is not None and last_update_age <= max(cfg["stale_after_sec"], 2 * tf_sec)
    closed = [b for b in bars if b["closed"]]
    live = bars[-1] if not bars[-1]["closed"] else None

    look = int(cfg["lookback_bars"])
    feats: list[dict[str, Any]] = []
    for i, bar in enumerate(closed):
        window = [b["volume"] or 0 for b in closed[max(0, i - look):i]]
        avg = sum(window) / len(window) if window else None
        feats.append(bar_features(bar, closed[i - 1] if i > 0 else None, avg))
    live_feat = None
    if live:
        window = [b["volume"] or 0 for b in closed[-look:]]
        live_feat = bar_features(live, closed[-1] if closed else None, sum(window) / len(window) if window else None)

    recent = feats[-3:]
    signals = {"long": [], "short": []}
    for f in recent:
        for side, names in long_short_signals(f).items():
            signals[side].extend(n for n in names if n not in signals[side])
    # The newest closed bar that printed any signal decides: an older flip must not hide a
    # newer one the other way (a short flip at 01:55 followed by a long flip at 02:00).
    latest = {"long": [], "short": [], "ts": None}
    for f in reversed(recent):
        sig = long_short_signals(f)
        if sig["long"] or sig["short"]:
            latest = {**sig, "ts": f.get("ts")}
            break

    cvd = 0.0
    cvd_series = []
    for b in closed[-look:]:
        cvd += b["delta"] or 0.0
        cvd_series.append(cvd)
    cvd_slope = cvd_series[-1] - cvd_series[-6] if len(cvd_series) >= 6 else None

    divergence, divergence_type = cvd_divergence(closed)

    return {
        "available": fresh,
        "reason": None if fresh else f"datos ATAS viejos ({last_update_age:.0f}s)" if last_update_age else "sin timestamp",
        "symbol": symbol.upper(),
        "tf": tf,
        "exchange": bars[-1].get("exchange"),
        "age_sec": round(last_update_age, 1) if last_update_age is not None else None,
        "closed_bars": len(closed),
        "last_closed": feats[-1] if feats else None,
        "live": live_feat,
        "recent_signals": signals,
        "latest_signals": latest,
        "cvd_window": round(cvd, 4),
        "cvd_slope_5": round(cvd_slope, 4) if cvd_slope is not None else None,
        "cvd_divergence": divergence,
        "cvd_divergence_type": divergence_type,
        "big_trades_15m": recent_big_trades(symbol, 15),
        "dom": latest_dom(symbol),
    }


_TF_PREFERENCE = ("5m", "15m", "1m", "3m", "30m", "1h", "4h")


def best_snapshot(symbol: str, tf: str) -> dict[str, Any]:
    """Snapshot for `tf`, or the freshest other ATAS chart of the same symbol (marked)."""
    exact = snapshot(symbol, tf)
    if exact.get("available"):
        return exact
    live_tfs = {f["tf"] for f in feeds() if f["symbol"] == symbol.upper() and (_age_sec(f["last_seen"]) or 1e9) <= _cfg()["stale_after_sec"]}
    for other in _TF_PREFERENCE:
        if other != tf and other in live_tfs:
            alt = snapshot(symbol, other)
            if alt.get("available"):
                alt["requested_tf"] = tf
                return alt
    return exact


def footprint_confirmation(atas: dict[str, Any], direction: str) -> dict[str, Any]:
    """Does the footprint of the last 3 closed bars confirm or contradict `direction`?"""
    if not atas.get("available") or direction not in ("long", "short"):
        return {"available": False, "confirmed": False, "contradicted": False, "signals": [], "against": []}
    other = "short" if direction == "long" else "long"
    # Decide on the newest bar with a signal (falls back to the 3-bar union for old payloads).
    basis = atas.get("latest_signals") or atas["recent_signals"]
    signals = basis.get(direction, [])
    against = basis.get(other, [])
    triggers = ("absorcion_en_minimo", "absorcion_en_maximo", "delta_reversal_intra_vela", "delta_flip")
    return {
        "available": True,
        # Absorption or a delta reversal is the core of the long/short trigger in the notes;
        # a stacked imbalance alone is continuation, not a reversal trigger. A bar that
        # signals both ways confirms nothing.
        "confirmed": any(s in signals for s in triggers) and not any(s in against for s in triggers),
        "contradicted": bool(against) and not any(s in signals for s in triggers),
        "signals": signals,
        "against": against,
        "bar": basis.get("ts"),
    }
