"""Causal detector and simulator for the Elliott 1-2 setup (end of wave 2 -> wave 3).

The rules are frozen in ``notes/setup-rules/elliott-1-2-reglas.md``. The machine only
proposes *geometric* 1-2 candidates; whether a candidate is a real 1-2 is the trader's
call, measured with the blind review (``agent/elliott_review.py``).

Causality: a zigzag pivot exists only from the bar that confirms it (price moved
``zigzag_atr`` x ATR away). Every decision uses closed bars up to that moment.
Shorts reuse the long logic on a mirrored price series (prices negated).
"""

from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from agent import backtest
from agent.config import resolve_path

REPORT_PREFIX = "elliott12_"


@dataclass(frozen=True)
class Elliott12Config:
    timeframe: str = "15m"
    zigzag_atr: float = 2.0
    min_w1_atr: float = 5.0
    zone_min: float = 0.5
    zone_max: float = 0.786
    limit_level: float = 0.618
    target_ext: float = 1.618
    stop_buffer_atr: float = 0.1
    w2_max_time_ratio: float = 4.0
    hold_w1_ratio: float = 3.0
    min_hold_bars: int = 20
    fee_rate_per_side: float = 0.0005
    slippage_rate_per_side: float = 0.0002
    htf: str = "4h"
    htf_ema: int = 50
    train_fraction: float = 0.70


# ---------------------------------------------------------------------------
# Pivots
# ---------------------------------------------------------------------------

def zigzag_pivots(high: np.ndarray, low: np.ndarray, atr: np.ndarray, threshold: float) -> list[dict[str, Any]]:
    """ATR zigzag. Each pivot carries ``confirmed``: the first bar where it is known."""
    n = len(high)
    valid = np.flatnonzero(np.isfinite(atr) & (atr > 0))
    if len(valid) == 0:
        return []
    start = int(valid[0])
    pivots: list[dict[str, Any]] = []
    direction = 0
    hi_i = lo_i = start
    for i in range(start + 1, n):
        a = atr[i]
        if not np.isfinite(a) or a <= 0:
            continue
        reach = threshold * a
        if direction == 0:
            if high[i] > high[hi_i]:
                hi_i = i
            if low[i] < low[lo_i]:
                lo_i = i
            if high[hi_i] - low[lo_i] >= reach:
                if lo_i < hi_i:
                    pivots.append({"kind": "L", "idx": lo_i, "price": float(low[lo_i]), "confirmed": i})
                    direction = 1
                else:
                    pivots.append({"kind": "H", "idx": hi_i, "price": float(high[hi_i]), "confirmed": i})
                    direction = -1
        elif direction == 1:
            if high[i] > high[hi_i]:
                hi_i = i
            elif high[hi_i] - low[i] >= reach:
                pivots.append({"kind": "H", "idx": hi_i, "price": float(high[hi_i]), "confirmed": i})
                direction = -1
                lo_i = i
        else:
            if low[i] < low[lo_i]:
                lo_i = i
            elif high[i] - low[lo_i] >= reach:
                pivots.append({"kind": "L", "idx": lo_i, "price": float(low[lo_i]), "confirmed": i})
                direction = 1
                hi_i = i
    return pivots


# ---------------------------------------------------------------------------
# Trade simulation (long-oriented arrays)
# ---------------------------------------------------------------------------

def _simulate(
    a: dict[str, np.ndarray],
    entry_idx: int,
    entry: float,
    stop: float,
    target: float,
    max_hold: int,
    cfg: Elliott12Config,
    fill_bar_stop_only: bool,
) -> dict[str, Any]:
    n = len(a["high"])
    final = min(entry_idx + max_hold - 1, n - 1)
    exit_idx, exit_price, reason = final, float(a["close"][final]), "time"
    for j in range(entry_idx, final + 1):
        stop_hit = a["low"][j] <= stop
        target_hit = a["high"][j] >= target and not (fill_bar_stop_only and j == entry_idx)
        if stop_hit:
            exit_idx, exit_price = j, stop
            reason = "same_bar_stop_first" if target_hit else "stop"
            break
        if target_hit:
            exit_idx, exit_price, reason = j, target, "target"
            break
    risk = entry - stop
    gross_r = (exit_price - entry) / risk
    mfe_r = (float(a["high"][entry_idx : exit_idx + 1].max()) - entry) / risk
    mae_r = (float(a["low"][entry_idx : exit_idx + 1].min()) - entry) / risk
    # Prices may be mirrored (negative) for shorts; costs use absolute price.
    round_trip = cfg.fee_rate_per_side + cfg.slippage_rate_per_side
    cost_r = round_trip * (abs(entry) + abs(exit_price)) / risk
    net_r = gross_r - cost_r
    return {
        "entry_idx": entry_idx,
        "exit_idx": exit_idx,
        "entry": entry,
        "stop": stop,
        "target": target,
        "exit": exit_price,
        "exit_reason": reason,
        "bars_held": exit_idx - entry_idx + 1,
        "planned_rr": (target - entry) / risk,
        "gross_r": gross_r,
        "cost_r": cost_r,
        "net_r": net_r,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "result": "win" if net_r > 1e-9 else ("loss" if net_r < -1e-9 else "breakeven"),
    }


def _variant_a(a, h1_idx, known, l0, h1, w1, atr_k, deadline, hold, cfg) -> dict[str, Any]:
    """Limit order at the 0.618 retracement, placed when the candidate becomes known."""
    n = len(a["high"])
    level = h1 - cfg.limit_level * w1
    for j in range(known + 1, min(deadline, n - 1) + 1):
        if a["low"][j] <= level:
            entry = min(level, float(a["open"][j]))
            stop = l0 - cfg.stop_buffer_atr * atr_k
            target = entry + cfg.target_ext * w1
            if entry <= stop:
                return {"status": "invalid_entry"}
            trade = _simulate(a, j, entry, stop, target, hold, cfg, fill_bar_stop_only=True)
            return {"status": "filled", "decision_idx": j - 1, **trade}
        if a["high"][j] > h1:
            return {"status": "missed"}
    return {"status": "expired"}


def _variant_b(a, h1_idx, known, l0, h1, w1, deadline, hold, cfg) -> dict[str, Any]:
    """Confirmation candle inside the 0.5-0.786 zone, entry at the next open."""
    n = len(a["high"])
    w2_low_idx = h1_idx + 1 + int(np.argmin(a["low"][h1_idx + 1 : known + 1]))
    w2_low = float(a["low"][w2_low_idx])
    reached_zone = False
    for j in range(known, min(deadline, n - 2) + 1):
        if j > known:
            if a["high"][j] > h1:
                return {"status": "missed_in_zone" if reached_zone else "missed_shallow"}
            if a["low"][j] < w2_low:
                w2_low, w2_low_idx = float(a["low"][j]), j
        depth = (h1 - w2_low) / w1
        if depth > cfg.zone_max:
            return {"status": "too_deep", "w2_depth": depth}
        if depth >= cfg.zone_min:
            reached_zone = True
            if (
                a["close"][j] > a["open"][j]
                and a["close"][j] > a["high"][j - 1]
                and a["delta"][j] > 0
                and a["close"][j] < h1
            ):
                atr_j = float(a["atr"][j])
                entry = float(a["open"][j + 1])
                stop = w2_low - cfg.stop_buffer_atr * atr_j
                target = w2_low + cfg.target_ext * w1
                if not (stop < entry < target):
                    return {"status": "invalid_entry"}
                trade = _simulate(a, j + 1, entry, stop, target, hold, cfg, fill_bar_stop_only=False)
                return {
                    "status": "triggered",
                    "decision_idx": j,
                    "w2_low_idx": w2_low_idx,
                    "w2_low": w2_low,
                    "w2_depth": depth,
                    **trade,
                }
    return {"status": "expired"}


def _wave3_label(a, known, l0, h1, horizon) -> bool | None:
    n = len(a["high"])
    for j in range(known + 1, min(horizon, n - 1) + 1):
        if a["low"][j] < l0:
            return False
        if a["high"][j] > h1:
            return True
    return None


def _features(a, l0_idx, h1_idx, decision, w2_low_idx) -> dict[str, Any]:
    vol = a["volume"]
    w1_vol = vol[l0_idx + 1 : h1_idx + 1]
    w2_vol = vol[h1_idx + 1 : decision + 1]
    w1_delta = float(a["delta"][l0_idx + 1 : h1_idx + 1].sum())
    vol_w2_w1 = float(w2_vol.mean() / w1_vol.mean()) if len(w1_vol) and len(w2_vol) and w1_vol.mean() > 0 else None
    absorption = False
    for k in (w2_low_idx, w2_low_idx + 1):
        if k <= decision and a["delta"][k] < 0 and a["close_location"][k] >= 0.5:
            absorption = True
    return {
        "vol_w2_w1": round(vol_w2_w1, 4) if vol_w2_w1 is not None else None,
        "delta_w1": round(w1_delta / float(w1_vol.sum()), 4) if w1_vol.sum() > 0 else None,
        "absorption_w2": absorption,
        "htf_aligned": bool(a["htf_aligned"][decision]),
        "w2_bars_ratio": round((decision - h1_idx) / max(1, h1_idx - l0_idx), 3),
    }


def _detect_side(a: dict[str, np.ndarray], side: str, cfg: Elliott12Config) -> list[dict[str, Any]]:
    pivots = zigzag_pivots(a["high"], a["low"], a["atr"], cfg.zigzag_atr)
    out: list[dict[str, Any]] = []
    for k in range(2, len(pivots)):
        hp, lp, hip = pivots[k - 2], pivots[k - 1], pivots[k]
        if not (hp["kind"] == "H" and lp["kind"] == "L" and hip["kind"] == "H"):
            continue
        l0_idx, h1_idx, known = lp["idx"], hip["idx"], hip["confirmed"]
        l0, h1 = lp["price"], hip["price"]
        w1 = h1 - l0
        atr_k = float(a["atr"][known])
        if w1 < cfg.min_w1_atr * atr_k:
            continue
        if float(a["close"][l0_idx : h1_idx + 1].max()) <= hp["price"]:
            continue  # wave 1 did not break structure with a body close
        w1_bars = max(1, h1_idx - l0_idx)
        deadline = h1_idx + math.ceil(cfg.w2_max_time_ratio * w1_bars)
        hold = max(cfg.min_hold_bars, math.ceil(cfg.hold_w1_ratio * w1_bars))
        var_a = _variant_a(a, h1_idx, known, l0, h1, w1, atr_k, deadline, hold, cfg)
        var_b = _variant_b(a, h1_idx, known, l0, h1, w1, deadline, hold, cfg)
        cand = {
            "side": side,
            "h_prev_idx": hp["idx"],
            "h_prev": hp["price"],
            "l0_idx": l0_idx,
            "l0": l0,
            "h1_idx": h1_idx,
            "h1": h1,
            "known_idx": known,
            "w1": w1,
            "w1_bars": w1_bars,
            "w1_atr": round(w1 / atr_k, 3),
            "wave3": _wave3_label(a, known, l0, h1, deadline + hold),
            "variant_a": var_a,
            "variant_b": var_b,
        }
        for var in (var_a, var_b):
            if "decision_idx" in var:
                w2_low_idx = var.get("w2_low_idx") or (
                    h1_idx + 1 + int(np.argmin(a["low"][h1_idx + 1 : var["decision_idx"] + 1]))
                )
                var["features"] = _features(a, l0_idx, h1_idx, var["decision_idx"], w2_low_idx)
        out.append(cand)
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _htf_alignment(df: pd.DataFrame, raw_1m: pd.DataFrame | None, cfg: Elliott12Config) -> np.ndarray:
    """+1 when the last closed HTF candle closes above its EMA, -1 below, 0 unknown."""
    if raw_1m is None or raw_1m.empty:
        return np.zeros(len(df))
    htf = backtest.resample_klines(raw_1m, cfg.htf)
    htf = htf.assign(ema=htf["close"].ewm(span=cfg.htf_ema, adjust=False, min_periods=cfg.htf_ema).mean())
    htf = htf.dropna(subset=["ema"])
    htf["state"] = np.sign(htf["close"] - htf["ema"])
    left = df[["close_time"]].reset_index()
    merged = pd.merge_asof(
        left.sort_values("close_time"),
        htf[["close_time", "state"]].sort_values("close_time"),
        on="close_time",
        direction="backward",
    ).sort_values("index")
    return merged["state"].fillna(0).to_numpy(dtype=float)


def _arrays(featured: pd.DataFrame, htf_state: np.ndarray, mirror: bool) -> dict[str, np.ndarray]:
    s = -1.0 if mirror else 1.0
    o, h, l, c = (featured[col].to_numpy(dtype=float) for col in ("open", "high", "low", "close"))
    rng = np.where(h - l > 0, h - l, np.nan)
    if mirror:
        h, l = l, h
    o, h, l, c = s * o, s * h, s * l, s * c
    cl = (c - l) / rng
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": featured["volume"].to_numpy(dtype=float),
        "delta": s * featured["delta"].to_numpy(dtype=float),
        "atr": featured["atr"].to_numpy(dtype=float),
        "close_location": np.nan_to_num(cl, nan=0.5),
        "htf_aligned": (s * htf_state) > 0,
    }


def _unmirror(value: Any, mirror: bool) -> Any:
    return -value if mirror and isinstance(value, float) else value


def detect_candidates(
    df: pd.DataFrame,
    cfg: Elliott12Config,
    symbol: str = "BTCUSDT",
    raw_1m: pd.DataFrame | None = None,
) -> list[dict[str, Any]]:
    """Return every geometric 1-2 candidate (long and short) with both variants simulated."""
    featured = backtest.add_features(df, volume_ratio=1.5, poi_lookback=30)
    htf_state = _htf_alignment(featured, raw_1m, cfg)
    open_times = featured["open_time"].tolist()
    close_times = featured["close_time"].tolist()
    split_idx = int(len(featured) * cfg.train_fraction)
    split_time = open_times[min(split_idx, len(open_times) - 1)]
    candidates: list[dict[str, Any]] = []
    for side, mirror in (("long", False), ("short", True)):
        arrays = _arrays(featured, htf_state, mirror)
        for cand in _detect_side(arrays, side, cfg):
            for key in ("h_prev", "l0", "h1"):
                cand[key] = _unmirror(cand[key], mirror)
            for idx_key in ("h_prev_idx", "l0_idx", "h1_idx", "known_idx"):
                cand[idx_key.replace("_idx", "_time")] = open_times[cand[idx_key]].isoformat()
            for var_name in ("variant_a", "variant_b"):
                var = cand[var_name]
                for key in ("entry", "stop", "target", "exit", "w2_low"):
                    if key in var:
                        var[key] = round(_unmirror(float(var[key]), mirror), 8)
                for key in ("gross_r", "cost_r", "net_r", "mfe_r", "mae_r", "planned_rr", "w2_depth"):
                    if key in var:
                        var[key] = round(float(var[key]), 6)
                if "decision_idx" in var:
                    var["decision_time"] = close_times[var["decision_idx"]].isoformat()
                if "entry_idx" in var:
                    var["entry_time"] = open_times[var["entry_idx"]].isoformat()
                    var["exit_time"] = close_times[var["exit_idx"]].isoformat()
                    var["sample"] = "test" if open_times[var["entry_idx"]] >= split_time else "train"
            cand["w1"] = round(abs(cand["w1"]), 8)
            cand["symbol"] = symbol.upper()
            cand["timeframe"] = cfg.timeframe
            cand["id"] = f"{symbol.upper()}-{cfg.timeframe}-{side}-{cand['h1_time']}"
            candidates.append(cand)
    candidates.sort(key=lambda c: c["known_time"])
    return candidates


def _trades(candidates: Iterable[dict[str, Any]], variant: str) -> list[dict[str, Any]]:
    out = []
    for cand in candidates:
        var = cand[variant]
        if "net_r" in var:
            out.append({**var, "side": cand["side"], "id": cand["id"]})
    return out


def _non_overlapping(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept, free_at = [], ""
    for trade in sorted(trades, key=lambda t: t["entry_time"]):
        if trade["entry_time"] >= free_at:
            kept.append(trade)
            free_at = trade["exit_time"]
    return kept


def metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    return backtest._trade_metrics(trades)


def _hypotheses(trades: list[dict[str, Any]]) -> dict[str, Any]:
    tests = {
        "V1_vol_w2_menor_w1": lambda f: f.get("vol_w2_w1") is not None and f["vol_w2_w1"] < 1.0,
        "V2_absorcion_low_w2": lambda f: bool(f.get("absorption_w2")),
        "H3_tendencia_4h": lambda f: bool(f.get("htf_aligned")),
    }
    out = {}
    for name, rule in tests.items():
        with_f = [t for t in trades if rule(t.get("features", {}))]
        without = [t for t in trades if not rule(t.get("features", {}))]
        out[name] = {"con": metrics(with_f), "sin": metrics(without)}
    return out


def summarize(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, dict[str, int]] = {}
    for variant in ("variant_a", "variant_b"):
        counts: dict[str, int] = {}
        for cand in candidates:
            status = cand[variant]["status"]
            counts[status] = counts.get(status, 0) + 1
        status_counts[variant] = counts
    reading = [c for c in candidates if c["variant_b"]["status"] in ("triggered", "missed_in_zone")]
    wave3_known = [c for c in reading if c["wave3"] is not None]
    variants = {}
    for variant in ("variant_a", "variant_b"):
        trades = _trades(candidates, variant)
        test = [t for t in trades if t["sample"] == "test"]
        variants[variant] = {
            "all": metrics(trades),
            "train": metrics([t for t in trades if t["sample"] == "train"]),
            "test": metrics(test),
            "long": metrics([t for t in trades if t["side"] == "long"]),
            "short": metrics([t for t in trades if t["side"] == "short"]),
            "non_overlapping": metrics(_non_overlapping(trades)),
            "hypotheses_all": _hypotheses(trades),
            "hypotheses_test": _hypotheses(test),
        }
    return {
        "candidates": len(candidates),
        "status": status_counts,
        "wave3_rate_in_zone": (
            round(100 * sum(1 for c in wave3_known if c["wave3"]) / len(wave3_known), 2) if wave3_known else None
        ),
        "wave3_sample": len(wave3_known),
        "variants": variants,
    }


def run(
    symbol: str = "BTCUSDT",
    timeframes: Iterable[str] = ("15m",),
    cfg: Elliott12Config | None = None,
    start_ms: int | None = None,
    end_ms: int | None = None,
    zigzag_values: Iterable[float] | None = None,
) -> dict[str, Any]:
    base = cfg or Elliott12Config()
    raw = backtest.load_history(symbol, "1m", start_ms, end_ms)
    runs = []
    all_candidates: list[dict[str, Any]] = []
    for timeframe in timeframes:
        df = backtest.resample_klines(raw, timeframe)
        for zz in zigzag_values or (base.zigzag_atr,):
            run_cfg = Elliott12Config(**{**asdict(base), "timeframe": timeframe, "zigzag_atr": float(zz)})
            candidates = detect_candidates(df, run_cfg, symbol, raw_1m=raw)
            primary = timeframe == base.timeframe and float(zz) == base.zigzag_atr
            runs.append({
                "config": asdict(run_cfg),
                "primary": primary,
                "data": {
                    "bars": len(df),
                    "from": df.iloc[0]["open_time"].isoformat(),
                    "to": df.iloc[-1]["close_time"].isoformat(),
                },
                "summary": summarize(candidates),
            })
            if primary:
                all_candidates = candidates
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol.upper(),
        "rules_note": "notes/setup-rules/elliott-1-2-reglas.md",
        "runs": runs,
        "candidates": all_candidates,
    }


def save(result: dict[str, Any], output_dir: str | Path | None = None) -> Path:
    if output_dir is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = resolve_path(f"reports/backtests/{REPORT_PREFIX}{stamp}")
    else:
        output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    summary = {k: v for k, v in result.items() if k != "candidates"}
    (output / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    (output / "candidates.json").write_text(json.dumps(result["candidates"], ensure_ascii=False), encoding="utf-8")
    fields = [
        "id", "side", "l0_time", "h1_time", "known_time", "l0", "h1", "w1", "w1_atr", "wave3",
        "a_status", "a_entry_time", "a_net_r", "b_status", "b_decision_time", "b_entry_time",
        "b_w2_depth", "b_net_r", "b_exit_reason", "vol_w2_w1", "delta_w1", "absorption_w2", "htf_aligned",
    ]
    with (output / "candidates.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for c in result["candidates"]:
            a, b = c["variant_a"], c["variant_b"]
            feats = b.get("features", {})
            writer.writerow({
                **{k: c.get(k) for k in fields[:10]},
                "a_status": a["status"], "a_entry_time": a.get("entry_time"), "a_net_r": a.get("net_r"),
                "b_status": b["status"], "b_decision_time": b.get("decision_time"),
                "b_entry_time": b.get("entry_time"), "b_w2_depth": b.get("w2_depth"),
                "b_net_r": b.get("net_r"), "b_exit_reason": b.get("exit_reason"),
                **{k: feats.get(k) for k in ("vol_w2_w1", "delta_w1", "absorption_w2", "htf_aligned")},
            })
    return output


def latest_run_dir() -> Path | None:
    root = resolve_path("reports/backtests")
    dirs = sorted(p for p in root.glob(f"{REPORT_PREFIX}*") if (p / "candidates.json").exists())
    return dirs[-1] if dirs else None
