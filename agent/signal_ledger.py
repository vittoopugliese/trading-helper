"""Signal ledger: every alert the platform sends is recorded with its evidence and resolved.

This is how the notes become measurable instead of believed. Each alert stores the
features present when it fired (footprint confirmation, liquidation flush, POI, grade,
OI, funding...). A resolver walks the 1m candles after the alert and labels it the same
way the backtest does (triple barrier: stop / target / timeout, stop first on the same
candle, fees + slippage per side). `stats()` then answers "does feature X raise the win
rate *and* the expectancy?" with Wilson 95% intervals, on signals that were never used to
tune anything (forward, out of sample by construction).

Entry is the price at alert time (what you can actually do when the alert arrives), not
the planned limit, so the label never assumes a fill the market might not have given.
"""

from __future__ import annotations

import json
import math
import sqlite3
from contextlib import contextmanager
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from agent.config import PROJECT_ROOT, get_config

_DB_LOCK = threading.Lock()

# Binary features worth comparing (present vs absent) once there are enough resolved signals.
FEATURES = (
    "atas_available",
    "atas_confirmed",
    "atas_contradicted",
    "liq_flush",
    "at_poi",
    "oi_rising",
    "funding_extreme",
    "trend_aligned",
    "context_fresh",
)


def _cfg() -> dict[str, Any]:
    defaults = {"cooldown_min": 30, "max_minutes": 240, "cost_bps_per_side": 7.0, "min_samples": 30}
    return {**defaults, **(get_config().get("signal_ledger") or {})}


def db_path() -> Path:
    return PROJECT_ROOT / "data" / "signals.db"


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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL, symbol TEXT NOT NULL, tf TEXT NOT NULL,
                kind TEXT NOT NULL, direction TEXT NOT NULL, grade TEXT, score REAL,
                price REAL NOT NULL, stop REAL NOT NULL, target REAL NOT NULL,
                planned_entry REAL, features TEXT NOT NULL, detail TEXT,
                status TEXT NOT NULL DEFAULT 'open', resolved_at TEXT,
                r_result REAL, mfe_r REAL, mae_r REAL, minutes INTEGER
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_signals_status ON signals(status)")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(
    *,
    symbol: str,
    tf: str,
    kind: str,
    direction: str,
    price: float,
    stop: float | None,
    target: float | None,
    grade: str | None = None,
    score: float | None = None,
    planned_entry: float | None = None,
    features: dict[str, Any] | None = None,
    detail: str = "",
    now: datetime | None = None,
) -> int | None:
    """Store an alert. Returns id, or None when invalid geometry or inside the cooldown."""
    if direction not in ("long", "short") or stop is None or target is None or price <= 0:
        return None
    if direction == "long" and not (stop < price < target):
        return None
    if direction == "short" and not (target < price < stop):
        return None
    now = now or _now()
    init_db()
    cooldown = timedelta(minutes=float(_cfg()["cooldown_min"]))
    with _DB_LOCK, _connect() as conn:
        recent = conn.execute(
            # Per kind: an observation must not mute a later eligible A+ of the same side.
            "SELECT 1 FROM signals WHERE symbol=? AND direction=? AND kind=? AND created_at>=? LIMIT 1",
            (symbol.upper(), direction, kind, (now - cooldown).isoformat()),
        ).fetchone()
        if recent:
            return None
        cur = conn.execute(
            """
            INSERT INTO signals (created_at, symbol, tf, kind, direction, grade, score, price, stop,
                                 target, planned_entry, features, detail)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (now.isoformat(), symbol.upper(), tf, kind, direction, grade, score, price, stop, target,
             planned_entry, json.dumps(features or {}, default=str), detail),
        )
        return cur.lastrowid


def label(
    direction: str, price: float, stop: float, target: float, candles: list[dict[str, float]],
    max_minutes: int, cost_bps: float,
) -> dict[str, Any] | None:
    """Triple-barrier label on 1m candles after the alert. None while still undecided."""
    risk = abs(price - stop)
    if risk <= 0:
        return {"status": "invalid", "r_result": 0.0, "mfe_r": 0.0, "mae_r": 0.0, "minutes": 0}
    cost_r = 2 * price * cost_bps / 10_000 / risk
    mfe = mae = 0.0
    for i, c in enumerate(candles[:max_minutes]):
        hi, lo = c["high"], c["low"]
        fav = (hi - price) if direction == "long" else (price - lo)
        adv = (price - lo) if direction == "long" else (hi - price)
        mfe, mae = max(mfe, fav / risk), max(mae, adv / risk)
        hit_stop = lo <= stop if direction == "long" else hi >= stop
        hit_target = hi >= target if direction == "long" else lo <= target
        if hit_stop:  # stop first when both are inside the same candle (conservative)
            return {"status": "loss", "r_result": round(-1 - cost_r, 4), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "minutes": i + 1}
        if hit_target:
            reward = abs(target - price) / risk
            return {"status": "win", "r_result": round(reward - cost_r, 4), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "minutes": i + 1}
    if len(candles) >= max_minutes:
        close = candles[max_minutes - 1]["close"]
        r = ((close - price) if direction == "long" else (price - close)) / risk
        return {"status": "timeout", "r_result": round(r - cost_r, 4), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3), "minutes": max_minutes}
    return None


def _fetch_1m_after(symbol: str, start: datetime, limit: int) -> list[dict[str, float]]:
    from agent import market_data

    start_ms = int(start.timestamp() * 1000)
    # Binance returns candles opening at/after startTime, so the candle the alert fired in
    # (whose high/low may predate the alert) is never included. Only closed candles count.
    rows = market_data.fetch_klines(symbol, "1m", limit=min(limit, 1000), start_time=start_ms)
    candles = [{"open_ms": int(r[0]), "high": float(r[2]), "low": float(r[3]), "close": float(r[4])} for r in rows]
    now_ms = int(_now().timestamp() * 1000)
    return [c for c in candles if c["open_ms"] >= start_ms and c["open_ms"] + 60_000 <= now_ms]


def resolve_open(fetch=_fetch_1m_after) -> int:
    """Label every open signal whose outcome is now known. Returns how many were resolved."""
    cfg = _cfg()
    init_db()
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM signals WHERE status='open'").fetchall()]
    done = 0
    for row in rows:
        created = datetime.fromisoformat(row["created_at"])
        try:
            candles = fetch(row["symbol"], created, int(cfg["max_minutes"]))
        except Exception:
            continue
        result = label(row["direction"], row["price"], row["stop"], row["target"], candles,
                       int(cfg["max_minutes"]), float(cfg["cost_bps_per_side"]))
        if result is None:
            continue
        with _DB_LOCK, _connect() as conn:
            conn.execute(
                "UPDATE signals SET status=?, r_result=?, mfe_r=?, mae_r=?, minutes=?, resolved_at=? WHERE id=?",
                (result["status"], result["r_result"], result["mfe_r"], result["mae_r"], result["minutes"],
                 _now().isoformat(), row["id"]),
            )
        done += 1
    return done


def wilson(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return round(centre - half, 4), round(centre + half, 4)


def _cohort(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    wins = sum(1 for r in rows if r["status"] == "win")
    rs = [r["r_result"] for r in rows if r["r_result"] is not None]
    return {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "win_rate_ci95": wilson(wins, n),
        "expectancy_r": round(sum(rs) / len(rs), 4) if rs else None,
        "total_r": round(sum(rs), 3) if rs else None,
    }


def stats(symbol: str | None = None) -> dict[str, Any]:
    """Forward results overall and split by each feature (present vs absent)."""
    init_db()
    query = "SELECT * FROM signals WHERE status IN ('win','loss','timeout')"
    args: tuple = ()
    if symbol:
        query += " AND symbol=?"
        args = (symbol.upper(),)
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute(query, args).fetchall()]
        open_count = conn.execute("SELECT COUNT(*) FROM signals WHERE status='open'").fetchone()[0]
    for r in rows:
        r["features"] = json.loads(r["features"] or "{}")

    min_samples = int(_cfg()["min_samples"])
    by_feature = {}
    for feat in FEATURES:
        yes = [r for r in rows if r["features"].get(feat) is True]
        no = [r for r in rows if r["features"].get(feat) is False]
        if yes or no:
            by_feature[feat] = {"con": _cohort(yes), "sin": _cohort(no),
                                "suficiente": len(yes) >= min_samples and len(no) >= min_samples}
    by_kind = {}
    for kind in sorted({r["kind"] for r in rows}):
        by_kind[kind] = _cohort([r for r in rows if r["kind"] == kind])
    return {
        "overall": _cohort(rows),
        "open": open_count,
        "min_samples": min_samples,
        "by_kind": by_kind,
        "by_feature": by_feature,
        "note": "Forward, sin optimizar. Una feature sirve si sube WR Y expectancy con IC que no se pisan, "
                f"con >= {min_samples} señales en cada grupo.",
    }


def recent(limit: int = 30) -> list[dict[str, Any]]:
    init_db()
    with _connect() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM signals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    for r in rows:
        r["features"] = json.loads(r["features"] or "{}")
    return rows
