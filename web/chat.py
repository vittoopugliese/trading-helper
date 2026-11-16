"""Claude BYOK chat with streaming for Trading Cockpit Web."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from dotenv import load_dotenv

from agent import analyst, knowledge, metrics
from agent.analyst import _sessions_db, init_sessions_db
from agent.config import PROJECT_ROOT
from agent.context import get_context
from web.services import analyze_for_api, find_chart_image, normalize_symbol

load_dotenv()


QUICK_PROMPTS = {
    "analyze_15m": (
        "Protocolo analiza en 5 min: trae order flow EN VIVO en 15m/5m/1m, confirma precio con ticker, "
        "evalua las 4 senales del gatillo en el POI del contexto, y da veredicto claro: "
        "ENTRADA MECANICA o que falta. Incluye entry, SL estructural, TP1 y sizing cuenta $300 x10."
    ),
    "ready": (
        "Estoy frente al grafico listo para ejecutar. Confirma o frena el gatillo AHORA con datos live. "
        "Se explicito con ENTRADA MECANICA si aplica."
    ),
    "sizing": (
        "Calcula sizing para la cuenta $300 leverage x10: SL estructural del contexto, "
        "riesgo $ objetivo $15-21 max, notional y margen. Muestra la formula."
    ),
    "register_trade": (
        "Ayudame a registrar el trade en el journal: pide entry, SL, TP, direccion, emocion y resultado si aplica."
    ),
    "analyze_books": (
        "Analisis de CONFLUENCIAS profundo usando la biblioteca de libros (Elliott, Wyckoff, "
        "order flow, psicologia). Cruza mi patron Elliott + el order flow live con los conceptos "
        "de los libros: donde estamos en el esquema Wyckoff (acumulacion/distribucion, spring/upthrust), "
        "personalidad de la onda actual, y que confluencias suman o restan grado. Cierra con veredicto "
        "claro (ENTRADA MECANICA o que falta) + entry, SL estructural, TP1 y sizing cuenta $300 x10."
    ),
}


def init_chat_db() -> None:
    init_sessions_db()
    with sqlite3.connect(_sessions_db()) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_chat_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                symbol TEXT,
                created_at TEXT NOT NULL
            )
            """
        )


def save_chat_message(role: str, content: str, symbol: str = "") -> None:
    init_chat_db()
    with sqlite3.connect(_sessions_db()) as conn:
        conn.execute(
            "INSERT INTO web_chat_messages (role, content, symbol, created_at) VALUES (?, ?, ?, ?)",
            (role, content, symbol, datetime.now(timezone.utc).isoformat()),
        )


def get_chat_history(limit: int = 30, symbol: str | None = None) -> list[dict[str, str]]:
    init_chat_db()
    query = "SELECT role, content, created_at FROM web_chat_messages"
    params: list[Any] = []
    if symbol:
        query += " WHERE symbol = ?"
        params.append(normalize_symbol(symbol))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with sqlite3.connect(_sessions_db()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [{"role": r[0], "content": r[1], "created_at": r[2]} for r in reversed(rows)]


def _load_rules_snippet() -> str:
    """Kept for callers: the full rules (no truncation), see agent/prompt_context."""
    from agent import prompt_context

    return prompt_context.rules_text()


def build_system_prompt(symbol: str, timeframe: str, trade_type: str, scope: str = "core",
                        question: str | None = None) -> str:
    """One analysis bundle (one search, with the user's question) + full policy and rules."""
    from agent import prompt_context

    sym = normalize_symbol(symbol)
    ctx = get_context(sym)
    bundle = analyze_for_api(sym, timeframe, trade_type, persist=False, scope=scope, question=question)
    n_notes = 8 if scope != "core" else 6
    ctx_text = json.dumps(ctx.to_dict() if ctx else {}, ensure_ascii=False, indent=2)
    if ctx:
        issues = ctx.validate()
        if issues or ctx.is_expired():
            ctx_text += "\nPROBLEMAS DEL CONTEXTO: " + "; ".join(
                issues + ([f"vencido ({ctx.age_days():.0f} dias)"] if ctx.is_expired() else [])
            )
    evidence = prompt_context.evidence_json(bundle.get("snapshot"), bundle.get("verdict"), bundle.get("price_live"))
    return f"""Eres el asistente de trading del proyecto trading-helper.
El trader opera crypto futures (Binance USDT-M) con Elliott + Order Flow.
Responde en espanol, directo, sin suavizar cuando hay gatillo claro.

{prompt_context.policy_block()}

Reglas del workspace (completas):
{prompt_context.rules_text()}

Contexto Elliott ({sym}):
{ctx_text}

Evidencia live ({timeframe} {trade_type}, precio usado = price_live):
{evidence}

{prompt_context.notes_block(bundle.get("relevant_notes") or [], limit=n_notes)}

Decision de entrada: di **ENTRADA MECANICA** solo si verdict.eligible es true y verdict.mechanical_entry es true.
Si verdict.blocked_reasons no esta vacio, explica cada motivo y que falta; no sugieras entrar.
El sizing sale de verdict.position_size (ya respeta la politica activa).

Datos de footprint: snapshot.atas viene del indicador ATAS (absorcion, delta reversal, imbalances
apilados, big trades, DOM). Si atas.available es false, deci "sin ATAS" y no inventes lectura de
footprint. snapshot.liquidations son liquidaciones reales (Bybit + Binance); si ready es false no
hay z-score. Este chat no tiene herramientas: no puede consultar datos nuevos, abrir notas ni
registrar trades; si falta un dato, pedilo o indica que hay que refrescar. La plataforma no envia
ordenes: solo analiza y alerta.
"""


def _image_block(chart_path: Path) -> dict[str, Any]:
    import base64

    ext = chart_path.suffix.lower().lstrip(".")
    media = "image/jpeg" if ext in ("jpg", "jpeg") else f"image/{ext}"
    data = base64.standard_b64encode(chart_path.read_bytes()).decode("ascii")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media, "data": data},
    }


def stream_chat(
    message: str,
    symbol: str,
    timeframe: str = "15m",
    trade_type: str = "scalp",
    *,
    include_chart: bool = True,
    quick_action: str | None = None,
    with_books: bool = False,
) -> Generator[str, None, None]:
    """Yield SSE data lines (text chunks and [DONE])."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        yield json.dumps({"error": "ANTHROPIC_API_KEY not set. Configure en Settings."})
        yield "[DONE]"
        return

    # El quick action "analyze_books" tambien fuerza la biblioteca.
    scope = "all" if (with_books or quick_action == "analyze_books") else "core"

    sym = normalize_symbol(symbol)
    user_msg = QUICK_PROMPTS.get(quick_action or "", message)
    if quick_action and message.strip():
        user_msg = f"{user_msg}\n\nContexto adicional del trader: {message}"

    save_chat_message("user", user_msg, sym)

    try:
        import anthropic
    except ImportError:
        yield json.dumps({"error": "anthropic package not installed"})
        yield "[DONE]"
        return

    client = anthropic.Anthropic(api_key=api_key)
    system = build_system_prompt(sym, timeframe, trade_type, scope=scope, question=user_msg)

    content: list[Any] = []
    if include_chart:
        chart = find_chart_image(sym)
        if chart:
            content.append(_image_block(chart))
    content.append({"type": "text", "text": user_msg})

    history = get_chat_history(limit=10, symbol=sym)
    messages: list[dict[str, Any]] = []
    for h in history[:-1]:
        if h["role"] in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": content})

    full = ""
    try:
        from agent import prompt_context

        with client.messages.stream(
            model=prompt_context.llm_model(),
            max_tokens=8000,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                full += text
                yield json.dumps({"text": text})
    except Exception as exc:
        yield json.dumps({"error": str(exc)})
        yield "[DONE]"
        return

    save_chat_message("assistant", full, sym)
    yield "[DONE]"
