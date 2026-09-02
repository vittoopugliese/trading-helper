"""Build evidence bundles and optional Claude deep analysis."""

from __future__ import annotations

import json
import math
import os
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.config import get_config, resolve_path
from agent.context import TradeContext, get_context, set_context
from agent import knowledge, metrics, setup_engine

load_dotenv()

VALID_TRADE_RESULTS = {"open", "win", "loss", "breakeven", "missed"}
VALID_CLOSE_RESULTS = {"win", "loss", "breakeven"}
VALID_DIRECTIONS = {"long", "short"}


class TradeNotFoundError(ValueError):
    pass


class TradeAlreadyClosedError(ValueError):
    pass


@dataclass
class EvidenceBundle:
    symbol: str
    timeframe: str
    trade_type: str
    snapshot: dict[str, Any]
    verdict: dict[str, Any]
    relevant_notes: list[dict[str, Any]] = field(default_factory=list)
    deep_analysis: str | None = None
    trade_context: dict[str, Any] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "trade_type": self.trade_type,
            "created_at": self.created_at,
            "snapshot": self.snapshot,
            "verdict": self.verdict,
            "relevant_notes": self.relevant_notes,
            "deep_analysis": self.deep_analysis,
            "trade_context": self.trade_context,
        }


def _sessions_db() -> Path:
    cfg = get_config()
    path = resolve_path(cfg["paths"]["sessions_db"])
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def init_sessions_db() -> None:
    db_path = _sessions_db()
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                grade TEXT NOT NULL,
                direction TEXT,
                score REAL,
                bundle_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS repl_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                trade_type TEXT NOT NULL,
                direction TEXT NOT NULL,
                wave TEXT,
                entry REAL,
                stop REAL,
                target REAL,
                rr_planned REAL,
                result TEXT,
                rr_real REAL,
                emotion TEXT,
                notes TEXT,
                created_at TEXT NOT NULL,
                closed_at TEXT
            );
            """
        )
        columns = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
        missing = [name for name in ("of_pattern", "elliott_alineado") if name not in columns]
        if missing:
            # One recoverable snapshot before the first schema migration. It is
            # intentionally not refreshed on later starts.
            backup = db_path.with_suffix(".db.bak-v1")
            if db_path.exists() and not backup.exists():
                conn.commit()
                with closing(sqlite3.connect(backup)) as backup_conn, backup_conn:
                    conn.backup(backup_conn)
            if "of_pattern" in missing:
                conn.execute("ALTER TABLE trades ADD COLUMN of_pattern TEXT")
            if "elliott_alineado" in missing:
                conn.execute("ALTER TABLE trades ADD COLUMN elliott_alineado INTEGER")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_trades_research
               ON trades(symbol, result, of_pattern, elliott_alineado)"""
        )
        conn.execute("PRAGMA user_version=1")


def save_analysis(bundle: EvidenceBundle) -> int:
    """Persist analysis to sessions.db."""
    init_sessions_db()
    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO analyses (symbol, timeframe, trade_type, grade, direction, score, bundle_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                bundle.symbol,
                bundle.timeframe,
                bundle.trade_type,
                bundle.verdict.get("grade"),
                bundle.verdict.get("direction"),
                bundle.verdict.get("score"),
                json.dumps(bundle.to_dict()),
                bundle.created_at,
            ),
        )
        return cursor.lastrowid or 0


def get_last_analysis(symbol: str, timeframe: str | None = None) -> dict[str, Any] | None:
    """Return most recent analysis for symbol."""
    init_sessions_db()
    query = "SELECT bundle_json FROM analyses WHERE symbol = ?"
    params: list[Any] = [symbol.upper()]
    if timeframe:
        query += " AND timeframe = ?"
        params.append(timeframe)
    query += " ORDER BY id DESC LIMIT 1"

    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def save_repl_message(role: str, content: str) -> None:
    init_sessions_db()
    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        conn.execute(
            "INSERT INTO repl_messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, datetime.now(timezone.utc).isoformat()),
        )


def get_repl_history(limit: int = 20) -> list[dict[str, str]]:
    init_sessions_db()
    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        rows = conn.execute(
            "SELECT role, content, created_at FROM repl_messages ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in reversed(rows)]


def log_trade(
    symbol: str,
    timeframe: str,
    trade_type: str,
    direction: str,
    entry: float | None = None,
    stop: float | None = None,
    target: float | None = None,
    rr_planned: float | None = None,
    wave: str = "",
    result: str = "open",
    rr_real: float | None = None,
    emotion: str = "",
    notes: str = "",
    of_pattern: str = "",
    elliott_alineado: bool | None = None,
) -> int:
    """Log a trade for journaling and effectiveness tracking."""
    direction = direction.lower().strip()
    result = result.lower().strip()
    if direction not in VALID_DIRECTIONS:
        raise ValueError("direction must be long or short")
    if result not in VALID_TRADE_RESULTS:
        raise ValueError(f"result must be one of {sorted(VALID_TRADE_RESULTS)}")
    for name, value in (("entry", entry), ("stop", stop), ("target", target), ("rr_planned", rr_planned), ("rr_real", rr_real)):
        if value is not None and not math.isfinite(value):
            raise ValueError(f"{name} must be finite")
    init_sessions_db()
    now = datetime.now(timezone.utc).isoformat()
    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        cursor = conn.execute(
            """
            INSERT INTO trades
            (symbol, timeframe, trade_type, direction, wave, entry, stop, target,
             rr_planned, result, rr_real, emotion, notes, created_at, closed_at,
             of_pattern, elliott_alineado)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                symbol.upper(),
                timeframe,
                trade_type,
                direction,
                wave,
                entry,
                stop,
                target,
                rr_planned,
                result,
                rr_real,
                emotion,
                notes,
                now,
                now if result != "open" else None,
                of_pattern.strip() or None,
                None if elliott_alineado is None else int(elliott_alineado),
            ),
        )
        return cursor.lastrowid or 0


def close_trade(trade_id: int, result: str, rr_real: float | None = None, emotion: str = "") -> dict[str, Any]:
    result = result.lower().strip()
    if result not in VALID_CLOSE_RESULTS:
        raise ValueError(f"result must be one of {sorted(VALID_CLOSE_RESULTS)}")
    if rr_real is not None and not math.isfinite(rr_real):
        raise ValueError("rr_real must be finite")
    init_sessions_db()
    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        cursor = conn.execute(
            """
            UPDATE trades SET result = ?, rr_real = ?, emotion = ?, closed_at = ?
            WHERE id = ? AND result = 'open'
            """,
            (result, rr_real, emotion, datetime.now(timezone.utc).isoformat(), trade_id),
        )
        if cursor.rowcount == 0:
            existing = conn.execute("SELECT result FROM trades WHERE id=?", (trade_id,)).fetchone()
            if existing is None:
                raise TradeNotFoundError(f"Trade #{trade_id} does not exist")
            raise TradeAlreadyClosedError(f"Trade #{trade_id} is already {existing[0]}")
    return {"id": trade_id, "result": result, "rr_real": rr_real, "closed": True}


def get_recent_trades(limit: int = 20, symbol: str | None = None) -> list[dict[str, Any]]:
    """Return most recent trades from journal."""
    init_sessions_db()
    query = """
        SELECT id, symbol, timeframe, trade_type, direction, wave, entry, stop, target,
               rr_planned, result, rr_real, emotion, notes, created_at, closed_at,
               of_pattern, elliott_alineado
        FROM trades
    """
    params: list[Any] = []
    if symbol:
        query += " WHERE symbol = ?"
        params.append(symbol.upper())
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)

    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        rows = conn.execute(query, params).fetchall()

    cols = [
        "id", "symbol", "timeframe", "trade_type", "direction", "wave",
        "entry", "stop", "target", "rr_planned", "result", "rr_real",
        "emotion", "notes", "created_at", "closed_at", "of_pattern", "elliott_alineado",
    ]
    return [dict(zip(cols, row)) for row in rows]


def _cohort_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Stats for executed/closed trades; missing R remains explicit, never imputed."""
    executed = [r for r in rows if r["result"] in VALID_CLOSE_RESULTS]
    wins = sum(r["result"] == "win" for r in executed)
    losses = sum(r["result"] == "loss" for r in executed)
    breakevens = sum(r["result"] == "breakeven" for r in executed)
    total = len(executed)
    decisive = wins + losses
    r_values = [float(r["rr_real"]) for r in executed if r["rr_real"] is not None]
    positive = sum(v for v in r_values if v > 0)
    negative = -sum(v for v in r_values if v < 0)
    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakevens": breakevens,
        "decisive_trades": decisive,
        "win_rate": round(wins / total * 100, 1) if total else 0.0,
        "win_rate_decisive": round(wins / decisive * 100, 1) if decisive else 0.0,
        "avg_r": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "expectancy_r": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "total_r": round(sum(r_values), 3),
        "avg_win_r": round(sum(v for v in r_values if v > 0) / sum(v > 0 for v in r_values), 3)
        if any(v > 0 for v in r_values) else None,
        "avg_loss_r": round(sum(v for v in r_values if v < 0) / sum(v < 0 for v in r_values), 3)
        if any(v < 0 for v in r_values) else None,
        "profit_factor": round(positive / negative, 3) if negative else None,
        "r_sample": len(r_values),
        "missing_r": total - len(r_values),
        "r_coverage_pct": round(len(r_values) / total * 100, 1) if total else 0.0,
    }


def _group_stats(rows: list[dict[str, Any]], key: str, unknown: str = "unknown") -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        value = row.get(key)
        label = unknown if value in (None, "") else str(value)
        grouped.setdefault(label, []).append(row)
    return {label: _cohort_stats(group) for label, group in sorted(grouped.items())}


def journal_stats(symbol: str | None = None) -> dict[str, Any]:
    """Return honest execution stats, including pattern/Elliott congruence cohorts."""
    init_sessions_db()
    query = """SELECT result, rr_real, rr_planned, wave, trade_type, direction,
                      of_pattern, elliott_alineado
               FROM trades WHERE result != 'open'"""
    params: list[Any] = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.upper())

    with closing(sqlite3.connect(_sessions_db())) as conn, conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return {"total": 0, "message": "No closed trades logged yet"}
    keys = ["result", "rr_real", "rr_planned", "wave", "trade_type", "direction", "of_pattern", "elliott_alineado"]
    records = [dict(zip(keys, row)) for row in rows]
    executed = [r for r in records if r["result"] in VALID_CLOSE_RESULTS]
    base = _cohort_stats(executed)
    base.update(
        {
            "total_records": len(records),
            "missed": sum(r["result"] == "missed" for r in records),
            "by_wave": _group_stats(executed, "wave"),
            "by_trade_type": _group_stats(executed, "trade_type"),
            "by_direction": _group_stats(executed, "direction"),
            "by_pattern": _group_stats(executed, "of_pattern"),
        }
    )
    for row in executed:
        raw = row["elliott_alineado"]
        row["elliott_alignment"] = "unknown" if raw is None else ("aligned" if raw else "not_aligned")
        pattern = row["of_pattern"] or "unknown"
        row["congruence"] = f"{pattern}|{row['elliott_alignment']}"
    base["by_elliott_alignment"] = _group_stats(executed, "elliott_alignment")
    base["by_congruence"] = _group_stats(executed, "congruence")
    return base


def run_deep_analysis(bundle: EvidenceBundle) -> str:
    """Optional Claude API reasoning with Elliott context + order flow."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        return "Deep analysis skipped: ANTHROPIC_API_KEY not set in .env"

    try:
        import anthropic
    except ImportError:
        return "Deep analysis skipped: anthropic package not installed"

    client = anthropic.Anthropic(api_key=api_key)

    ctx_section = ""
    if bundle.trade_context:
        ctx_section = f"""
Elliott/Fib context (user-defined — this is the primary bias):
{json.dumps(bundle.trade_context, indent=2, ensure_ascii=False)}
"""

    notes_section = ""
    if bundle.relevant_notes:
        snippets = []
        for n in bundle.relevant_notes[:6]:
            snippets.append(f"- [{n.get('title')}] ({n.get('source_file')}): {(n.get('content') or '')[:400]}")
        notes_section = "Relevant notes from knowledge base:\n" + "\n".join(snippets)

    prompt = f"""You are a disciplined crypto futures analyst specializing in Elliott Waves + Order Flow.
The trader has more Elliott/Fib experience; you complement with order flow data and psychology.

Rules:
- The user's Elliott context (wave count, POI, invalidation) takes priority over generic structure
- Only recommend A+ if confluence is strong (checklist A+ from notes)
- Cite specific conditions from the notes when relevant
- Include R:R assessment and 2% risk sizing commentary
- Address trading psychology: patience, invalidation acceptance, not forcing trades
- Never suggest automatic execution
- Be objective, no hype

{ctx_section}
{notes_section}

Market evidence:
{json.dumps({"snapshot": bundle.snapshot, "verdict": bundle.verdict}, indent=2, default=str)}

Respond in Spanish with:
1. Veredicto (A+ / B / NO TRADE) — aligned with but may refine the engine score
2. Elliott: onda actual, confluencia Fib, POI status
3. Order Flow: delta/CVD real, book, patterns at POI (absorcion/sweep/drenaje)
4. Niveles: entry, stop, target, R:R, position size (2% rule)
5. Riesgos / invalidacion / divergencia CVD (especialmente onda 5)
6. Psicologia: que hacer si no hay setup, gestion emocional
7. Nivel de confianza (alta/media/baja)
"""

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def analyze(
    symbol: str,
    timeframe: str,
    trade_type: str = "swing",
    deep: bool = False,
    compare_previous: bool = False,
    context_override: dict[str, Any] | None = None,
    persist_context: bool = False,
    scope: str = "core",
) -> EvidenceBundle:
    """Full analysis pipeline with optional Elliott context.

    scope controla la base de conocimiento que se recupera:
      - "core" (default): tus guidelines (order flow + elliott + setup-rules), SIN libros.
        Analisis rapido y enfocado en volumen + tu patron.
      - "all": suma la biblioteca de libros (Wyckoff/Elliott/psicologia) para un pase
        de estudio mas profundo de confluencias.
      - "books": solo la biblioteca de libros.
    """
    ctx = get_context(symbol, override=context_override)
    if persist_context and context_override:
        ctx = set_context(symbol, context_override)

    snapshot = metrics.build_snapshot(symbol, timeframe, trade_type, trade_context=ctx)
    verdict = setup_engine.evaluate_setup(snapshot, trade_context=ctx)

    query = knowledge.build_search_query(snapshot.to_dict(), ctx.to_dict() if ctx else None)
    notes = knowledge.search_notes(query, scope=scope)

    bundle = EvidenceBundle(
        symbol=symbol.upper(),
        timeframe=timeframe,
        trade_type=trade_type,
        snapshot=snapshot.to_dict(),
        verdict=verdict.to_dict(),
        relevant_notes=notes,
        trade_context=ctx.to_dict() if ctx else None,
    )

    if compare_previous:
        prev = get_last_analysis(symbol.upper(), timeframe)
        if prev:
            bundle.relevant_notes.insert(
                0,
                {
                    "title": "Previous analysis comparison",
                    "content": json.dumps(
                        {
                            "previous_grade": prev.get("verdict", {}).get("grade"),
                            "previous_direction": prev.get("verdict", {}).get("direction"),
                            "previous_score": prev.get("verdict", {}).get("score"),
                            "previous_time": prev.get("created_at"),
                        },
                        indent=2,
                    ),
                    "source_file": "sessions.db",
                    "category": "history",
                    "relevance": 1.0,
                },
            )

    if deep:
        bundle.deep_analysis = run_deep_analysis(bundle)

    save_analysis(bundle)
    return bundle


def analyze_with_levels(
    symbol: str,
    timeframe: str,
    bias: str,
    poi_low: float,
    poi_high: float,
    invalidation: float,
    targets: list[float],
    trade_type: str = "swing",
    wave: str = "",
    notes: str = "",
    deep: bool = False,
    persist: bool = True,
    scope: str = "core",
) -> EvidenceBundle:
    """Analyze with ephemeral Elliott levels from chat; optionally persist to context file."""
    override = {
        "bias": bias,
        "wave": wave,
        "trade_type": trade_type,
        "poi": {"low": poi_low, "high": poi_high},
        "invalidation": invalidation,
        "targets": targets,
        "notes": notes,
    }
    return analyze(
        symbol,
        timeframe,
        trade_type,
        deep=deep,
        context_override=override,
        persist_context=persist,
        scope=scope,
    )
