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
from agent.execution import ExecutionError, ExecutionService, load_limits
from web.services import analyze_for_api, find_chart_image, normalize_symbol

load_dotenv()

MODEL = "claude-opus-4-8"

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


PROPOSE_TOOL = {
    "name": "proponer_orden",
    "description": (
        "Crea una PROPUESTA de orden limite post-only (maker) con SL y TP adjuntos en BingX. "
        "NO ejecuta nada: queda pendiente hasta que el trader la aprueba con un click en el cockpit, "
        "y el sistema re-valida los limites duros antes de enviarla. El tamano se calcula solo desde "
        "el riesgo maximo configurado; no lo propongas vos. Usala cuando hay ENTRADA MECANICA con "
        "niveles estructurales concretos o cuando el trader pide explicitamente armar la orden. "
        "Devuelve la propuesta con qty, riesgo, R:R, comisiones y la lista de errores si un limite la bloquea."
    ),
    "eager_input_streaming": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "side": {"type": "string", "enum": ["long", "short"]},
            "entry": {"type": "number", "description": "Precio limite de entrada (debe quedar del lado maker del libro)."},
            "stop": {"type": "number", "description": "SL estructural (invalidacion)."},
            "take_profit": {"type": "number", "description": "TP1."},
            "rationale": {"type": "string", "description": "Por que: confluencias y gatillo, en una o dos frases."},
        },
        "required": ["side", "entry", "stop", "take_profit", "rationale"],
        "additionalProperties": False,
    },
}
MAX_TOOL_ROUNDS = 3


def _valid_proposal_input(args: Any) -> bool:
    if not isinstance(args, dict) or args.get("side") not in ("long", "short"):
        return False
    for key in ("entry", "stop", "take_profit"):
        value = args.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            return False
    return isinstance(args.get("rationale", ""), str)


def _run_propose_tool(args: dict[str, Any], symbol: str, timeframe: str) -> tuple[dict[str, Any] | None, str]:
    """Returns (record or None, tool_result text for the model)."""
    try:
        record = ExecutionService.instance().propose(
            symbol=symbol,
            side=args["side"],
            entry=float(args["entry"]),
            stop=float(args["stop"]),
            take_profit=float(args["take_profit"]),
            timeframe=timeframe,
            source="chat",
            rationale=args.get("rationale", ""),
        )
    except ExecutionError as exc:
        return None, json.dumps({"error": str(exc)}, ensure_ascii=False)
    summary = {
        "id": record["id"],
        "status": record["status"],
        "mode": record["mode"],
        "plan": record["plan"],
        "errors": record["errors"],
        "warnings": record["warnings"],
        "nota": "Pendiente de aprobacion del trader en el cockpit. No digas que se ejecuto.",
    }
    return record, json.dumps(summary, ensure_ascii=False, default=str)


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
    rules_dir = PROJECT_ROOT / ".cursor" / "rules"
    parts: list[str] = []
    for name in ("inicio-sesion.mdc", "entrada-mecanica.mdc", "analisis-5min-gatillo.mdc"):
        path = rules_dir / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8")[:2000])
    return "\n\n".join(parts)


def build_system_prompt(symbol: str, timeframe: str, trade_type: str, scope: str = "core") -> str:
    sym = normalize_symbol(symbol)
    ctx = get_context(sym)
    bundle = analyze_for_api(sym, timeframe, trade_type, persist=False, scope=scope)
    notes = knowledge.search_notes(
        knowledge.build_search_query(bundle.get("snapshot", {}), bundle.get("trade_context")),
        scope=scope,
    )
    n_notes = 8 if scope != "core" else 5
    notes_label = "Notas relevantes (incluye biblioteca de libros)" if scope != "core" else "Notas relevantes"
    notes_text = "\n".join(
        f"- [{n.get('title')}] {(n.get('content') or '')[:300]}" for n in notes[:n_notes]
    )
    ctx_text = json.dumps(ctx.to_dict() if ctx else {}, ensure_ascii=False, indent=2)
    evidence = json.dumps(
        {"snapshot": bundle.get("snapshot"), "verdict": bundle.get("verdict")},
        indent=2,
        default=str,
    )
    rules = _load_rules_snippet()
    limits = load_limits()
    exec_mode = "DEMO VST, dinero ficticio" if not limits.is_live else "LIVE, dinero real"
    exec_risk = limits.max_risk_usd
    exec_rr = limits.min_rr
    exec_symbols = "simbolos habilitados: " + ", ".join(limits.symbols)
    return f"""Eres el asistente de trading del proyecto trading-helper.
El trader opera crypto futures (Binance USDT-M) con Elliott + Order Flow.
Responde en espanol, directo, sin suavizar cuando hay gatillo claro.

Reglas del workspace:
{rules}

Contexto Elliott ({sym}):
{ctx_text}

Evidencia live ({timeframe} {trade_type}):
{evidence}

{notes_label}:
{notes_text}

Cuando haya confluencia >=3 criterios y sin invalidacion, di **ENTRADA MECANICA** con entry, SL, TP1 y sizing.
Margen != riesgo. Tope $21 riesgo por trade en cuenta $300 x10.

Ejecucion BingX ({exec_mode}): podes usar la herramienta proponer_orden para dejar una orden limite
post-only lista para aprobar. Limites duros del ejecutor: riesgo max ${exec_risk} por trade, R:R min {exec_rr},
{exec_symbols}. Vos solo propones; el trader aprueba con un click. Nunca digas que una orden fue enviada.
Si la herramienta devuelve errores, explicalos y no insistas con niveles inventados para esquivar un limite.
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
    system = build_system_prompt(sym, timeframe, trade_type, scope=scope)

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
    for _ in range(MAX_TOOL_ROUNDS + 1):
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=4000,
                system=system,
                messages=messages,
                tools=[PROPOSE_TOOL],
            ) as stream:
                for text in stream.text_stream:
                    full += text
                    yield json.dumps({"text": text})
                response = stream.get_final_message()
        except ValueError:
            # Tool input JSON the SDK could not parse; stop instead of guessing.
            yield json.dumps({"error": "La propuesta del modelo llego malformada. Pedila de nuevo."})
            break
        except Exception as exc:
            yield json.dumps({"error": str(exc)})
            yield "[DONE]"
            return

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses or response.stop_reason in ("refusal", "max_tokens"):
            break
        results = []
        for block in tool_uses:
            if block.name != PROPOSE_TOOL["name"] or not _valid_proposal_input(block.input):
                results.append({"type": "tool_result", "tool_use_id": block.id, "is_error": True,
                                "content": "Input invalido: side long/short y entry/stop/take_profit numeros positivos."})
                continue
            record, tool_text = _run_propose_tool(block.input, sym, timeframe)
            if record is not None:
                yield json.dumps({"proposal": record}, default=str)
                full += f"\n[Propuesta #{record['id']} {record['status']}]\n"
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": tool_text})
        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": results})

    save_chat_message("assistant", full, sym)
    yield "[DONE]"
