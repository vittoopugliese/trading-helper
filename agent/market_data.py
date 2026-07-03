"""Binance Futures USDT-M market data fetch and SQLite cache."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from agent.config import get_config, resolve_path

INTERVAL_MS = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}


def _db_path() -> Path:
    cfg = get_config()
    path = resolve_path(cfg["paths"]["cache_db"])
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def init_cache_db() -> None:
    """Create cache tables if they do not exist."""
    with sqlite3.connect(_db_path()) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS klines (
                symbol TEXT NOT NULL,
                interval TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                volume REAL NOT NULL,
                close_time INTEGER NOT NULL,
                quote_volume REAL NOT NULL,
                trades INTEGER NOT NULL,
                taker_buy_base REAL NOT NULL,
                taker_buy_quote REAL NOT NULL,
                PRIMARY KEY (symbol, interval, open_time)
            );
            CREATE INDEX IF NOT EXISTS idx_klines_symbol_interval
                ON klines(symbol, interval, open_time);
            """
        )


def _base_url() -> str:
    return get_config()["binance"]["base_url"].rstrip("/")


def fetch_klines(symbol: str, interval: str, limit: int = 500, start_time: int | None = None) -> list[list[Any]]:
    """Fetch klines from Binance Futures public API."""
    params: dict[str, Any] = {"symbol": symbol.upper(), "interval": interval, "limit": limit}
    if start_time is not None:
        params["startTime"] = start_time

    url = f"{_base_url()}/fapi/v1/klines"
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def _parse_kline_row(symbol: str, interval: str, row: list[Any]) -> tuple[Any, ...]:
    return (
        symbol.upper(),
        interval,
        int(row[0]),
        float(row[1]),
        float(row[2]),
        float(row[3]),
        float(row[4]),
        float(row[5]),
        int(row[6]),
        float(row[7]),
        int(row[8]),
        float(row[9]),
        float(row[10]),
    )


def save_klines(symbol: str, interval: str, rows: list[list[Any]]) -> int:
    """Insert or replace kline rows. Returns count saved."""
    if not rows:
        return 0

    parsed = [_parse_kline_row(symbol, interval, r) for r in rows]
    with sqlite3.connect(_db_path()) as conn:
        conn.executemany(
            """
            INSERT OR REPLACE INTO klines
            (symbol, interval, open_time, open, high, low, close, volume,
             close_time, quote_volume, trades, taker_buy_base, taker_buy_quote)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            parsed,
        )
    return len(parsed)


def get_last_open_time(symbol: str, interval: str) -> int | None:
    """Return the latest cached open_time for symbol/interval."""
    with sqlite3.connect(_db_path()) as conn:
        row = conn.execute(
            "SELECT MAX(open_time) FROM klines WHERE symbol = ? AND interval = ?",
            (symbol.upper(), interval),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def update_cache(symbol: str, interval: str, limit: int | None = None) -> int:
    """Fetch new klines since last cache and store them."""
    init_cache_db()
    cfg = get_config()
    if limit is None:
        limit = cfg["candle_limits"].get(interval, 200)

    last = get_last_open_time(symbol, interval)
    if last is None:
        rows = fetch_klines(symbol, interval, limit=limit)
    else:
        start = last + INTERVAL_MS.get(interval, 60_000)
        rows = fetch_klines(symbol, interval, limit=limit, start_time=start)
        if not rows:
            rows = fetch_klines(symbol, interval, limit=limit)

    return save_klines(symbol, interval, rows)


def load_klines_df(symbol: str, interval: str, limit: int | None = None) -> pd.DataFrame:
    """Load cached klines as a pandas DataFrame, updating cache first."""
    cfg = get_config()
    if limit is None:
        limit = cfg["candle_limits"].get(interval, 200)

    update_cache(symbol, interval, limit=limit)

    with sqlite3.connect(_db_path()) as conn:
        df = pd.read_sql_query(
            """
            SELECT open_time, open, high, low, close, volume, close_time,
                   quote_volume, trades, taker_buy_base, taker_buy_quote
            FROM klines
            WHERE symbol = ? AND interval = ?
            ORDER BY open_time DESC
            LIMIT ?
            """,
            conn,
            params=(symbol.upper(), interval, limit),
        )

    if df.empty:
        return df

    df = df.sort_values("open_time").reset_index(drop=True)
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
    return df


def fetch_open_interest(symbol: str) -> dict[str, Any]:
    """Fetch current open interest."""
    url = f"{_base_url()}/fapi/v1/openInterest"
    response = requests.get(url, params={"symbol": symbol.upper()}, timeout=30)
    response.raise_for_status()
    data = response.json()
    return {
        "symbol": data["symbol"],
        "open_interest": float(data["openInterest"]),
        "time": datetime.fromtimestamp(data["time"] / 1000, tz=timezone.utc).isoformat(),
    }


def fetch_open_interest_hist(symbol: str, period: str = "1h", limit: int = 30) -> list[dict[str, Any]]:
    """Fetch historical open interest."""
    url = f"{_base_url()}/futures/data/openInterestHist"
    params = {"symbol": symbol.upper(), "period": period, "limit": limit}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return [
        {
            "timestamp": int(item["timestamp"]),
            "open_interest": float(item["sumOpenInterest"]),
            "open_interest_value": float(item["sumOpenInterestValue"]),
        }
        for item in response.json()
    ]


def fetch_funding_rate(symbol: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fetch recent funding rates."""
    url = f"{_base_url()}/fapi/v1/fundingRate"
    params = {"symbol": symbol.upper(), "limit": limit}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return [
        {
            "funding_time": int(item["fundingTime"]),
            "funding_rate": float(item["fundingRate"]),
        }
        for item in response.json()
    ]


def fetch_long_short_ratio(symbol: str, period: str = "1h", limit: int = 30) -> list[dict[str, Any]]:
    """Fetch top trader long/short account ratio."""
    url = f"{_base_url()}/futures/data/topLongShortAccountRatio"
    params = {"symbol": symbol.upper(), "period": period, "limit": limit}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return [
        {
            "timestamp": int(item["timestamp"]),
            "long_short_ratio": float(item["longShortRatio"]),
            "long_account": float(item["longAccount"]),
            "short_account": float(item["shortAccount"]),
        }
        for item in response.json()
    ]


def fetch_ticker_price(symbol: str) -> float:
    """Fetch latest mark price."""
    url = f"{_base_url()}/fapi/v1/ticker/price"
    response = requests.get(url, params={"symbol": symbol.upper()}, timeout=30)
    response.raise_for_status()
    return float(response.json()["price"])


def fetch_agg_trades(symbol: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Fetch recent aggregate trades for real delta/CVD."""
    cfg = get_config()
    if limit is None:
        limit = cfg.get("order_flow", {}).get("agg_trades_limit", 1000)

    url = f"{_base_url()}/fapi/v1/aggTrades"
    params: dict[str, Any] = {"symbol": symbol.upper(), "limit": min(limit, 1000)}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    return [
        {
            "id": int(item["a"]),
            "price": float(item["p"]),
            "qty": float(item["q"]),
            "time": int(item["T"]),
            "is_buyer_maker": bool(item["m"]),
        }
        for item in response.json()
    ]


def fetch_depth(symbol: str, limit: int | None = None) -> dict[str, Any]:
    """Fetch order book depth snapshot."""
    cfg = get_config()
    if limit is None:
        limit = cfg.get("order_flow", {}).get("depth_limit", 20)

    url = f"{_base_url()}/fapi/v1/depth"
    params = {"symbol": symbol.upper(), "limit": limit}
    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()
    data = response.json()
    return {
        "bids": [[float(p), float(q)] for p, q in data.get("bids", [])],
        "asks": [[float(p), float(q)] for p, q in data.get("asks", [])],
        "last_update_id": data.get("lastUpdateId"),
    }
