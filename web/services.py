"""Shared helpers for web API — multi-TF, BTC context, config IO."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from agent import market_data, metrics
from agent.config import PROJECT_ROOT, get_config, resolve_path
from agent.watcher import enrich_verdict_dict


def normalize_symbol(symbol: str) -> str:
    sym = symbol.upper().strip()
    if not sym.endswith("USDT"):
        sym += "USDT"
    return sym


def multi_tf_report(symbol: str, tfs: list[str] | None = None) -> dict[str, Any]:
    """Per-TF order flow summary for cockpit."""
    if tfs is None:
        tfs = ["1m", "5m", "15m", "1h"]
    out: dict[str, Any] = {}
    ticker = market_data.fetch_ticker_price(symbol)

    for tf in tfs:
        market_data.update_cache(symbol, tf, limit=200)
        df = market_data.load_klines_df(symbol, tf, limit=200)
        delta = metrics.compute_delta_series(df)
        df = df.copy()
        df["delta"] = delta
        kprice = float(df["close"].iloc[-1])
        last8 = df.tail(8)
        ind = metrics.compute_indicators(df)
        struct = metrics.compute_structure(df)
        divergence = None
        if len(df) >= 10:
            cvd = delta.cumsum()
            price_chg = kprice - float(df["close"].iloc[-10])
            cvd_slope = float(cvd.iloc[-1] - cvd.iloc[-10])
            if price_chg < 0 and cvd_slope > 0:
                divergence = "bullish"
            elif price_chg > 0 and cvd_slope < 0:
                divergence = "bearish"

        stale = abs(kprice - ticker) / ticker > 0.01 if ticker else False
        lc = df.iloc[-1]
        buy_ratio = float(lc["taker_buy_base"] / lc["volume"]) if lc["volume"] else 0.5

        out[tf] = {
            "close": round(kprice, 4),
            "ticker": round(ticker, 4),
            "stale_cache": stale,
            "trend": struct.get("trend"),
            "rsi": ind.get("rsi"),
            "vol_ratio": ind.get("volume_ratio"),
            "buy_ratio": round(buy_ratio, 4),
            "delta_sum_8": round(float(last8["delta"].sum()), 0),
            "swing_high": struct.get("last_swing_high"),
            "swing_low": struct.get("last_swing_low"),
            "divergence": divergence,
        }
    return out


def btc_context() -> dict[str, Any]:
    """BTC 1h macro context for alt correlation banner."""
    symbol = "BTCUSDT"
    market_data.update_cache(symbol, "1h", limit=100)
    df = market_data.load_klines_df(symbol, "1h", limit=100)
    delta = metrics.compute_delta_series(df)
    struct = metrics.compute_structure(df)
    ind = metrics.compute_indicators(df)
    price = market_data.fetch_ticker_price(symbol)
    delta8 = float(delta.tail(8).sum())
    trend = struct.get("trend", "neutral")
    favorable_for_long = trend in ("bullish", "neutral") and delta8 >= 0
    return {
        "symbol": symbol,
        "price": price,
        "trend": trend,
        "rsi": ind.get("rsi"),
        "delta_8h": round(delta8, 0),
        "favorable_for_alts_long": favorable_for_long,
        "label": "viento a favor" if favorable_for_long else "viento en contra / neutro",
    }


def chart_asset_dir(symbol: str) -> Path:
    path = resolve_path("context/assets") / normalize_symbol(symbol)
    path.mkdir(parents=True, exist_ok=True)
    return path


def find_chart_image(symbol: str) -> Path | None:
    folder = chart_asset_dir(symbol)
    for ext in ("png", "jpg", "jpeg", "webp"):
        p = folder / f"chart.{ext}"
        if p.exists():
            return p
    return None


def save_chart_image(symbol: str, content: bytes, filename: str) -> Path:
    ext = Path(filename).suffix.lower().lstrip(".") or "png"
    if ext not in ("png", "jpg", "jpeg", "webp"):
        raise ValueError("Unsupported image format")
    folder = chart_asset_dir(symbol)
    for old in folder.glob("chart.*"):
        old.unlink(missing_ok=True)
    dest = folder / f"chart.{ext if ext != 'jpeg' else 'jpg'}"
    dest.write_bytes(content)
    return dest


def load_env_file() -> dict[str, str]:
    env_path = PROJECT_ROOT / ".env"
    values: dict[str, str] = {}
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        values[k.strip()] = v.strip().strip('"').strip("'")
    return values


def write_env_key(key: str, value: str) -> None:
    env_path = PROJECT_ROOT / ".env"
    lines: list[str] = []
    found = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


def read_settings() -> dict[str, Any]:
    cfg = get_config()
    risk = cfg.get("risk", {})
    env = load_env_file()
    return {
        "has_anthropic_key": bool(env.get("ANTHROPIC_API_KEY")),
        "has_bingx_keys": bool(env.get("BINGX_API_KEY") and env.get("BINGX_API_SECRET")),
        "execution_mode": (cfg.get("execution") or {}).get("mode", "demo"),
        "account_size": risk.get("default_account_size", 300),
        "leverage": risk.get("leverage", 10),
        "max_risk_usd": risk.get("max_risk_usd", 21),
        "max_risk_pct": risk.get("max_risk_pct", 7.0),
        "web_port": cfg.get("web", {}).get("port", 8780),
    }


def update_risk_settings(data: dict[str, Any]) -> dict[str, Any]:
    cfg_path = PROJECT_ROOT / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    risk = cfg.setdefault("risk", {})
    for k in ("default_account_size", "max_risk_usd", "leverage"):
        if data.get(k) is not None:
            risk[k] = data[k]
    with open(cfg_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
    from agent.config import reset_config

    reset_config()
    return read_settings()


def analyze_for_api(
    symbol: str,
    timeframe: str,
    trade_type: str,
    *,
    persist: bool = True,
    scope: str = "core",
) -> dict[str, Any]:
    from agent import analyst

    bundle = analyst.analyze(
        symbol, timeframe, trade_type, deep=False, compare_previous=False, scope=scope
    )
    if not persist:
        pass
    data = bundle.to_dict()
    verdict = data.get("verdict") or {}
    hunt = [verdict.get("direction", "long")]
    if verdict.get("direction") == "none":
        hunt = ["long", "short"]
    buy_ratio = (data.get("snapshot") or {}).get("order_flow", {}).get("buy_ratio")
    verdict["_buy_ratio"] = buy_ratio
    data["verdict"] = enrich_verdict_dict(verdict, hunt)
    data["price_live"] = market_data.fetch_ticker_price(symbol)
    return data
