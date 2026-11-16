"""Blind review of Elliott 1-2 candidates: the trader decides, then the outcome is revealed.

Candidates come from the latest ``reports/backtests/elliott12_*`` run (variant B,
confirmation entry). The chart sent before the decision ends at the decision candle,
has no dates and shows prices as % from the decision close, so the historical moment
cannot be recognised. Decisions are final (one row per candidate).

Edge = result of the candidates you took - result of every candidate you reviewed.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
import threading
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from agent import backtest, elliott_backtest, market_data
from agent.config import resolve_path

CONTEXT_BARS = 300
REVEAL_EXTRA_BARS = 40
DECISIONS = ("take", "skip")
COUNT_LABELS = ("12_claro", "12_con_alternativo", "abc_o_flat", "diagonal", "no_se")

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"dir": None, "candidates": {}}


def db_path() -> Path:
    return resolve_path("data/elliott_review.db")


def _connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS reviews (
            candidate_id TEXT PRIMARY KEY,
            run_dir TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            decision TEXT NOT NULL,
            count_label TEXT,
            confidence INTEGER,
            note TEXT
        )
        """
    )
    return conn


class ReviewError(ValueError):
    pass


def token(candidate_id: str) -> str:
    """Opaque id for the browser: the real id contains the date."""
    return hashlib.sha1(candidate_id.encode("utf-8")).hexdigest()[:16]


def _candidates() -> dict[str, dict[str, Any]]:
    run_dir = elliott_backtest.latest_run_dir()
    if run_dir is None:
        raise ReviewError("No hay corrida Elliott 1-2. Correr `python main.py elliott-backtest` primero.")
    if _CACHE["dir"] != str(run_dir):
        data = json.loads((run_dir / "candidates.json").read_text(encoding="utf-8"))
        _CACHE["candidates"] = {
            c["id"]: c for c in data if c["variant_b"]["status"] == "triggered"
        }
        _CACHE["dir"] = str(run_dir)
    return _CACHE["candidates"]


def _reviewed() -> dict[str, dict[str, Any]]:
    with closing(_connect()) as conn:
        return {row["candidate_id"]: dict(row) for row in conn.execute("SELECT * FROM reviews")}


def _bars(cand: dict[str, Any], until: pd.Timestamp, extra_after: int = 0) -> pd.DataFrame:
    tf_ms = market_data.INTERVAL_MS[cand["timeframe"]]
    first = min(pd.Timestamp(cand["h_prev_time"]), until - pd.Timedelta(milliseconds=tf_ms * CONTEXT_BARS))
    first = first - pd.Timedelta(milliseconds=tf_ms * 30)
    last = until + pd.Timedelta(milliseconds=tf_ms * (extra_after + 1))
    raw = market_data.load_cached_klines_df(
        cand["symbol"], "1m", int(first.timestamp() * 1000), int(last.timestamp() * 1000)
    )
    df = backtest.resample_klines(raw, cand["timeframe"])
    df["delta"] = 2.0 * df["taker_buy_base"] - df["volume"]
    return df


def _norm(price: float | None, ref: float) -> float | None:
    return None if price is None else round((float(price) / ref - 1.0) * 100.0, 4)


def _series(df: pd.DataFrame, ref: float, vol_ref: float, base_index: int) -> list[dict[str, Any]]:
    out = []
    for i, row in enumerate(df.itertuples(index=False)):
        out.append({
            "i": base_index + i,
            "o": _norm(row.open, ref),
            "h": _norm(row.high, ref),
            "l": _norm(row.low, ref),
            "c": _norm(row.close, ref),
            "v": round(float(row.volume) / vol_ref, 4),
            "d": round(float(row.delta) / vol_ref, 4),
        })
    return out


def _bar_index(df: pd.DataFrame, when: str, column: str = "open_time") -> int | None:
    hits = np.flatnonzero((df[column] == pd.Timestamp(when)).to_numpy())
    return int(hits[0]) if len(hits) else None


def _payload(cand: dict[str, Any], reveal: bool) -> dict[str, Any]:
    b = cand["variant_b"]
    decision_close = pd.Timestamp(b["decision_time"])
    exit_time = pd.Timestamp(b["exit_time"])
    tf_ms = market_data.INTERVAL_MS[cand["timeframe"]]
    after = 0
    if reveal:
        after = int((exit_time - decision_close).total_seconds() * 1000 // tf_ms) + REVEAL_EXTRA_BARS
    df = _bars(cand, decision_close, after)
    decision_rows = np.flatnonzero((df["close_time"] <= decision_close).to_numpy())
    if len(decision_rows) == 0:
        raise ReviewError("Sin velas en cache para este candidato")
    d_idx = int(decision_rows[-1])
    if not reveal:
        df = df.iloc[: d_idx + 1]
    ref = float(df.iloc[d_idx]["close"])
    vol_ref = float(df.iloc[: d_idx + 1]["volume"].mean()) or 1.0
    marks = {}
    for key in ("h_prev", "l0", "h1"):
        idx = _bar_index(df, cand[f"{key}_time"])
        if idx is not None:
            marks[key] = {"i": idx - d_idx, "price": _norm(cand[key], ref)}
    sign = 1 if cand["side"] == "long" else -1
    h1, w1 = float(cand["h1"]), float(cand["w1"])
    fibs = {str(lvl): _norm(h1 - sign * lvl * w1, ref) for lvl in (0.5, 0.618, 0.786)}
    payload: dict[str, Any] = {
        "id": token(cand["id"]),
        "side": cand["side"],
        "timeframe": cand["timeframe"],
        "bars": _series(df, ref, vol_ref, -d_idx),
        "marks": marks,
        "fibs": fibs,
        "plan": {
            "entry_ref": 0.0,
            "stop": _norm(b["stop"], ref),
            "target": _norm(b["target"], ref),
            "planned_rr": b["planned_rr"],
        },
    }
    if reveal:
        payload["outcome"] = {
            "symbol": cand["symbol"],
            "date": decision_close.isoformat(),
            "entry": _norm(b["entry"], ref),
            "exit": _norm(b["exit"], ref),
            "exit_reason": b["exit_reason"],
            "net_r": b["net_r"],
            "mfe_r": b["mfe_r"],
            "result": b["result"],
            "wave3": cand["wave3"],
            "exit_i": (_bar_index(df, b["exit_time"], "close_time") or d_idx) - d_idx,
            "w2_depth": b.get("w2_depth"),
        }
    return payload


def next_candidate(seed: int | None = None) -> dict[str, Any]:
    cands = _candidates()
    done = _reviewed()
    pending = [cid for cid in cands if cid not in done]
    if not pending:
        raise ReviewError("No quedan candidatos sin revisar en esta corrida.")
    cid = random.Random(seed).choice(pending)
    payload = _payload(cands[cid], reveal=False)
    payload["progress"] = {"reviewed": len(done), "total": len(cands)}
    return payload


def decide(candidate_id: str, decision: str, count_label: str | None, confidence: int | None, note: str | None) -> dict[str, Any]:
    if decision not in DECISIONS:
        raise ReviewError(f"decision debe ser {DECISIONS}")
    if count_label is not None and count_label not in COUNT_LABELS:
        raise ReviewError(f"count_label debe ser {COUNT_LABELS}")
    if confidence is not None and confidence not in (1, 2, 3):
        raise ReviewError("confidence debe ser 1, 2 o 3")
    cands = _candidates()
    by_token = {token(cid): cid for cid in cands}
    candidate_id = by_token.get(candidate_id, "")
    cand = cands.get(candidate_id)
    if cand is None:
        raise ReviewError("Candidato desconocido")
    with _LOCK, closing(_connect()) as conn, conn:
        try:
            conn.execute(
                "INSERT INTO reviews VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate_id,
                    _CACHE["dir"],
                    datetime.now(timezone.utc).isoformat(),
                    decision,
                    count_label,
                    confidence,
                    (note or "")[:500],
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ReviewError("Ya decidiste este candidato; la decisión no se cambia.") from exc
    return _payload(cand, reveal=True)


def _group(cands: list[dict[str, Any]]) -> dict[str, Any]:
    trades = [{**c["variant_b"], "side": c["side"]} for c in cands]
    m = backtest._trade_metrics(trades)
    w3 = [c["wave3"] for c in cands if c["wave3"] is not None]
    m["wave3_rate"] = round(100 * sum(w3) / len(w3), 2) if w3 else None
    return m


def stats() -> dict[str, Any]:
    cands = _candidates()
    done = _reviewed()
    reviewed = [cands[cid] for cid in done if cid in cands]
    taken = [cands[cid] for cid, row in done.items() if cid in cands and row["decision"] == "take"]
    skipped = [cands[cid] for cid, row in done.items() if cid in cands and row["decision"] == "skip"]
    by_conf = {}
    for level in (1, 2, 3):
        group = [cands[cid] for cid, row in done.items() if cid in cands and row["decision"] == "take" and row["confidence"] == level]
        by_conf[str(level)] = _group(group)
    by_label = {}
    for label in COUNT_LABELS:
        group = [cands[cid] for cid, row in done.items() if cid in cands and row["count_label"] == label]
        by_label[label] = _group(group)
    taken_m, reviewed_m = _group(taken), _group(reviewed)
    edge = None
    if taken_m["expectancy_r"] is not None and reviewed_m["expectancy_r"] is not None:
        edge = round(taken_m["expectancy_r"] - reviewed_m["expectancy_r"], 4)
    return {
        "run_dir": _CACHE["dir"],
        "progress": {"reviewed": len(reviewed), "total": len(cands)},
        "taken": taken_m,
        "skipped": _group(skipped),
        "reviewed": reviewed_m,
        "all_candidates": _group(list(cands.values())),
        "edge_expectancy_r": edge,
        "taken_by_confidence": by_conf,
        "by_count_label": by_label,
    }
