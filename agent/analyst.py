"""Build evidence bundles and optional Claude deep analysis."""

from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from agent.config import get_config, resolve_path
from agent.context import TradeContext, get_context, set_context
from agent import knowledge, metrics, setup_engine

load_dotenv()


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
    with sqlite3.connect(_sessions_db()) as conn:
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


def save_analysis(bundle: EvidenceBundle) -> int:
    """Persist analysis to sessions.db."""
    init_sessions_db()
    with sqlite3.connect(_sessions_db()) as conn:
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

    with sqlite3.connect(_sessions_db()) as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        return None
    return json.loads(row[0])


def save_repl_message(role: str, content: str) -> None:
    init_sessions_db()
    with sqlite3.connect(_sessions_db()) as conn:
        conn.execute(
            "INSERT INTO repl_messages (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, datetime.now(timezone.utc).isoformat()),
        )


def get_repl_history(limit: int = 20) -> list[dict[str, str]]:
    init_sessions_db()
    with sqlite3.connect(_sessions_db()) as conn:
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
) -> int:
    """Log a trade for journaling and effectiveness tracking."""
    init_sessions_db()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(_sessions_db()) as conn:
        cursor = conn.execute(
            """
            INSERT INTO trades
            (symbol, timeframe, trade_type, direction, wave, entry, stop, target,
             rr_planned, result, rr_real, emotion, notes, created_at, closed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        return cursor.lastrowid or 0


def close_trade(trade_id: int, result: str, rr_real: float | None = None, emotion: str = "") -> None:
    init_sessions_db()
    with sqlite3.connect(_sessions_db()) as conn:
        conn.execute(
            """
            UPDATE trades SET result = ?, rr_real = ?, emotion = ?, closed_at = ?
            WHERE id = ?
            """,
            (result, rr_real, emotion, datetime.now(timezone.utc).isoformat(), trade_id),
        )


def journal_stats(symbol: str | None = None) -> dict[str, Any]:
    """Return win rate, avg R, stats by wave and trade type."""
    init_sessions_db()
    query = "SELECT result, rr_real, rr_planned, wave, trade_type, direction FROM trades WHERE result != 'open'"
    params: list[Any] = []
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol.upper())

    with sqlite3.connect(_sessions_db()) as conn:
        rows = conn.execute(query, params).fetchall()

    if not rows:
        return {"total": 0, "message": "No closed trades logged yet"}

    wins = sum(1 for r in rows if r[0] == "win")
    losses = sum(1 for r in rows if r[0] == "loss")
    total = len(rows)
    rr_values = [r[1] for r in rows if r[1] is not None]
    avg_r = round(sum(rr_values) / len(rr_values), 2) if rr_values else 0.0

    by_wave: dict[str, dict[str, int]] = {}
    for r in rows:
        wave = r[3] or "unknown"
        by_wave.setdefault(wave, {"win": 0, "loss": 0})
        if r[0] == "win":
            by_wave[wave]["win"] += 1
        elif r[0] == "loss":
            by_wave[wave]["loss"] += 1

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / total * 100, 1) if total else 0,
        "avg_r": avg_r,
        "by_wave": by_wave,
    }


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
        for n in bundle.relevant_notes[:5]:
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
) -> EvidenceBundle:
    """Full analysis pipeline with optional Elliott context."""
    ctx = get_context(symbol, override=context_override)
    if persist_context and context_override:
        ctx = set_context(symbol, context_override)

    snapshot = metrics.build_snapshot(symbol, timeframe, trade_type, trade_context=ctx)
    verdict = setup_engine.evaluate_setup(snapshot, trade_context=ctx)

    query = knowledge.build_search_query(snapshot.to_dict(), ctx.to_dict() if ctx else None)
    notes = knowledge.search_notes(query)

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
    )
