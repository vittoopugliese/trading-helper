"""Live liquidation feed (Bybit allLiquidation + Binance forceOrder) -> data/liquidations.db.

This is the "different information" the backtest review asked for: who is being forced
out of positions right now. Public websockets, no keys.

- Bybit `allLiquidation.{symbol}`: every liquidation, pushed every 500 ms. `S=Buy` means a
  LONG position was liquidated (per Bybit docs).
- Binance `{symbol}@forceOrder`: at most the latest liquidation per symbol per second (a
  sample, not the total). `S=SELL` is the forced sell of a liquidated LONG.

Side stored = the position that was liquidated: 'long' or 'short'. A long-liquidation flush
into support is forced selling that has already happened (the "fuel was spent" idea).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
import statistics
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from agent.config import PROJECT_ROOT, get_config

log = logging.getLogger(__name__)

BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
BINANCE_WS = "wss://fstream.binance.com/stream?streams="

_DB_LOCK = threading.Lock()


def db_path() -> Path:
    return PROJECT_ROOT / "data" / "liquidations.db"


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
            CREATE TABLE IF NOT EXISTS liquidations (
                venue TEXT NOT NULL, symbol TEXT NOT NULL, ts_ms INTEGER NOT NULL,
                side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, usd REAL NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS ix_liq_symbol_ts ON liquidations(symbol, ts_ms)")


def parse_bybit(message: dict[str, Any]) -> list[tuple]:
    if not str(message.get("topic", "")).startswith("allLiquidation."):
        return []
    rows = []
    for item in message.get("data") or []:
        try:
            qty, price = float(item["v"]), float(item["p"])
            side = "long" if item["S"] == "Buy" else "short"
            rows.append(("bybit", item["s"], int(item["T"]), side, qty, price, qty * price))
        except (KeyError, TypeError, ValueError):
            continue
    return rows


def parse_binance(message: dict[str, Any]) -> list[tuple]:
    data = message.get("data", message)
    if data.get("e") != "forceOrder":
        return []
    order = data.get("o") or {}
    try:
        qty = float(order.get("z") or order["q"])
        price = float(order.get("ap") or order["p"])
        side = "long" if order["S"] == "SELL" else "short"
        return [("binance", order["s"], int(order["T"]), side, qty, price, qty * price)]
    except (KeyError, TypeError, ValueError):
        return []


def store(rows: list[tuple]) -> None:
    if not rows:
        return
    with _DB_LOCK, _connect() as conn:
        conn.executemany("INSERT INTO liquidations VALUES (?,?,?,?,?,?,?)", rows)


def _bucket_totals(rows: list[sqlite3.Row], start_ms: int, end_ms: int, bucket_ms: int) -> list[float]:
    n = max(1, (end_ms - start_ms) // bucket_ms)
    buckets = [0.0] * n
    for r in rows:
        i = (r["ts_ms"] - start_ms) // bucket_ms
        if 0 <= i < n:
            buckets[i] += r["usd"]
    return buckets


def summary(symbol: str, now: datetime | None = None) -> dict[str, Any]:
    """Liquidation pressure for `symbol`: USD by side over 5/15/60 min + 15m z-score vs 24h."""
    init_db()
    now = now or datetime.now(timezone.utc)
    end_ms = int(now.timestamp() * 1000)
    start_ms = end_ms - 24 * 3600 * 1000
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM liquidations WHERE symbol=? AND ts_ms>=? AND ts_ms<=?",
            (symbol.upper(), start_ms, end_ms),
        ).fetchall()

    out: dict[str, Any] = {"symbol": symbol.upper(), "events_24h": len(rows)}
    for minutes in (5, 15, 60):
        since = end_ms - minutes * 60_000
        win = [r for r in rows if r["ts_ms"] >= since]
        out[f"long_usd_{minutes}m"] = round(sum(r["usd"] for r in win if r["side"] == "long"), 2)
        out[f"short_usd_{minutes}m"] = round(sum(r["usd"] for r in win if r["side"] == "short"), 2)

    coverage_h = (end_ms - min(r["ts_ms"] for r in rows)) / 3_600_000 if rows else 0.0
    out["coverage_hours"] = round(coverage_h, 2)
    cfg = get_config().get("liquidations") or {}
    min_hours = float(cfg.get("min_history_hours", 6))
    z_thr = float(cfg.get("spike_zscore", 2.5))
    for side in ("long", "short"):
        side_rows = [r for r in rows if r["side"] == side]
        hist = _bucket_totals(side_rows, start_ms, end_ms - 15 * 60_000, 15 * 60_000)
        current = out[f"{side}_usd_15m"]
        z = None
        if coverage_h >= min_hours and len(hist) >= 8:
            mean = statistics.fmean(hist)
            sd = statistics.pstdev(hist)
            z = (current - mean) / sd if sd > 0 else (None if current == 0 else 10.0)
        out[f"{side}_z_15m"] = round(z, 2) if z is not None else None
        out[f"{side}_spike"] = bool(z is not None and z >= z_thr and current > 0)
    out["ready"] = coverage_h >= min_hours
    return out


def flush_confirmation(liq: dict[str, Any], direction: str) -> dict[str, Any]:
    """Long after a long-liquidation flush (forced sellers spent); short after a short squeeze."""
    if not liq or not liq.get("ready"):
        return {"available": False, "flush": False, "detail": "historial de liquidaciones insuficiente"}
    side = "long" if direction == "long" else "short"
    spike = bool(liq.get(f"{side}_spike"))
    usd = liq.get(f"{side}_usd_15m", 0)
    z = liq.get(f"{side}_z_15m")
    return {
        "available": True,
        "flush": spike,
        "detail": f"liq {side}s 15m ${usd:,.0f} (z={z})",
    }


class LiquidationService:
    """Background websocket readers. One thread per venue, reconnects with backoff."""

    _instance: "LiquidationService | None" = None

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.status: dict[str, Any] = {"bybit": "off", "binance": "off"}

    @classmethod
    def instance(cls) -> "LiquidationService":
        if cls._instance is None:
            cls._instance = LiquidationService()
        return cls._instance

    def symbols(self) -> list[str]:
        cfg = get_config().get("liquidations") or {}
        return [s.upper() for s in cfg.get("symbols", ["HYPEUSDT", "BTCUSDT"])]

    def start(self) -> None:
        cfg = get_config().get("liquidations") or {}
        if not cfg.get("enabled", True) or any(t.is_alive() for t in self._threads):
            return
        init_db()
        self._stop.clear()
        self._threads = [
            threading.Thread(target=self._run, args=("bybit",), daemon=True, name="liq-bybit"),
            threading.Thread(target=self._run, args=("binance",), daemon=True, name="liq-binance"),
        ]
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self, venue: str) -> None:
        from websockets.sync.client import connect

        backoff = 2.0
        while not self._stop.is_set():
            try:
                if venue == "bybit":
                    url = BYBIT_WS
                else:
                    url = BINANCE_WS + "/".join(f"{s.lower()}@forceOrder" for s in self.symbols())
                with connect(url, open_timeout=10, close_timeout=2) as ws:
                    if venue == "bybit":
                        ws.send(json.dumps({"op": "subscribe", "args": [f"allLiquidation.{s}" for s in self.symbols()]}))
                    self.status[venue] = "connected"
                    backoff = 2.0
                    last_ping = time.monotonic()
                    while not self._stop.is_set():
                        if venue == "bybit" and time.monotonic() - last_ping > 20:
                            ws.send(json.dumps({"op": "ping"}))
                            last_ping = time.monotonic()
                        try:
                            raw = ws.recv(timeout=5)
                        except TimeoutError:
                            continue
                        message = json.loads(raw)
                        rows = parse_bybit(message) if venue == "bybit" else parse_binance(message)
                        store(rows)
            except Exception as exc:  # network errors: keep retrying, never crash the web app
                self.status[venue] = f"error: {exc.__class__.__name__}"
                log.debug("liquidation feed %s: %s", venue, exc)
            if self._stop.wait(backoff):
                break
            backoff = min(backoff * 2, 60.0)
        self.status[venue] = "off"


def prune(days: int = 30) -> None:
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    with _DB_LOCK, _connect() as conn:
        conn.execute("DELETE FROM liquidations WHERE ts_ms < ?", (cutoff,))
