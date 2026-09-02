"""FastAPI server for Trading Cockpit Web."""

from __future__ import annotations

import asyncio
import json
import queue
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agent import analyst, context as trade_context, market_data, metrics
from agent.config import PROJECT_ROOT, get_config
from agent.watcher import WatcherService
from web.chat import get_chat_history, stream_chat
from web.schemas import (
    AnthropicKeyUpdate,
    ChatRequest,
    ContextUpdate,
    JournalTradeCreate,
    RiskSettingsUpdate,
    WatcherStartRequest,
)
from web.services import (
    analyze_for_api,
    btc_context,
    find_chart_image,
    multi_tf_report,
    normalize_symbol,
    read_settings,
    save_chart_image,
    update_risk_settings,
    write_env_key,
)

STATIC_DIR = Path(__file__).parent / "static"
APP_VERSION = "1.0.0"

app = FastAPI(title="Trading Cockpit", version=APP_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:8780",
        "http://localhost:8780",
        "http://127.0.0.1:8781",
        "http://localhost:8781",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_event_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=500)
_watcher = WatcherService.instance()


def _on_watcher_event(event: dict[str, Any]) -> None:
    try:
        _event_queue.put_nowait(event)
    except queue.Full:
        pass


_watcher.add_listener(_on_watcher_event)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "version": APP_VERSION}


@app.get("/api/ticker/{symbol}")
def ticker(symbol: str) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    try:
        price = market_data.fetch_ticker_price(sym)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"symbol": sym, "price": price, "ts": datetime.now(timezone.utc).isoformat()}


@app.get("/api/snapshot/{symbol}")
def snapshot(
    symbol: str,
    timeframe: str = Query("15m"),
    trade_type: str = Query("scalp"),
) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    market_data.update_cache(sym, timeframe, limit=200)
    ctx = trade_context.get_context(sym)
    snap = metrics.build_snapshot(sym, timeframe, trade_type, trade_context=ctx)
    snap.price = market_data.fetch_ticker_price(sym)
    data = snap.to_dict()
    data["price_live"] = snap.price
    return data


@app.get("/api/analyze/{symbol}")
def analyze(
    symbol: str,
    timeframe: str = Query("15m"),
    trade_type: str = Query("scalp"),
    persist: bool = Query(True),
) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    try:
        return analyze_for_api(sym, timeframe, trade_type, persist=persist)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/multi-tf/{symbol}")
def multi_tf(symbol: str, tfs: str = Query("1m,5m,15m,1h")) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    tf_list = [t.strip() for t in tfs.split(",") if t.strip()]
    try:
        return multi_tf_report(sym, tf_list)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/btc-context")
def api_btc_context() -> dict[str, Any]:
    try:
        return btc_context()
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/context/{symbol}")
def get_ctx(symbol: str) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    ctx = trade_context.get_context(sym)
    if ctx is None:
        raise HTTPException(404, f"No context for {sym}")
    data = ctx.to_dict()
    if find_chart_image(sym):
        data["has_chart"] = True
    return data


@app.put("/api/context/{symbol}")
def put_ctx(symbol: str, body: ContextUpdate) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if "poi_low" in payload or "poi_high" in payload:
        payload["poi"] = {"low": body.poi_low, "high": body.poi_high}
        payload.pop("poi_low", None)
        payload.pop("poi_high", None)
    ctx = trade_context.set_context(sym, payload)
    return ctx.to_dict()


@app.post("/api/context/{symbol}/chart")
async def upload_chart(symbol: str, file: UploadFile = File(...)) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    if not file.filename:
        raise HTTPException(400, "Missing filename")
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 5MB)")
    try:
        path = save_chart_image(sym, content, file.filename)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    rel = path.relative_to(PROJECT_ROOT).as_posix()
    trade_context.set_context(sym, {"chart_image": rel})
    return {"ok": True, "path": rel}


@app.get("/api/context/{symbol}/chart")
def get_chart(symbol: str):
    sym = normalize_symbol(symbol)
    path = find_chart_image(sym)
    if not path:
        raise HTTPException(404, "No chart image")
    return FileResponse(path)


@app.post("/api/watcher/start")
def watcher_start(body: WatcherStartRequest) -> dict[str, Any]:
    sym = normalize_symbol(body.symbol)
    status = _watcher.start(
        sym,
        body.timeframe,
        scalp=body.scalp,
        bias=body.bias,
        interval_sec=body.interval_sec,
    )
    return status.to_dict()


@app.post("/api/watcher/stop")
def watcher_stop() -> dict[str, Any]:
    return _watcher.stop().to_dict()


@app.get("/api/watcher/status")
def watcher_status() -> dict[str, Any]:
    return _watcher.status().to_dict()


@app.get("/api/watcher/last-scan")
def watcher_last_scan() -> dict[str, Any]:
    scan = _watcher.last_scan()
    if scan is None:
        scan = _watcher.run_once()
    if scan is None:
        raise HTTPException(404, "No scan available")
    return scan.to_dict()


@app.get("/api/events")
async def events_stream():
    async def generate():
        yield f"data: {json.dumps({'type': 'connected', 'ts': datetime.now(timezone.utc).isoformat()})}\n\n"
        while True:
            try:
                event = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _event_queue.get(timeout=25)
                )
                yield f"data: {json.dumps(event, default=str)}\n\n"
            except queue.Empty:
                yield f"data: {json.dumps({'type': 'ping'})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@app.get("/api/journal/stats")
def journal_stats(symbol: str | None = Query(None)) -> dict[str, Any]:
    return analyst.journal_stats(symbol.upper() if symbol else None)


@app.get("/api/journal/recent")
def journal_recent(limit: int = Query(20, ge=1, le=100), symbol: str | None = Query(None)) -> list[dict]:
    return analyst.get_recent_trades(limit=limit, symbol=symbol.upper() if symbol else None)


@app.post("/api/journal/trade")
def journal_create(body: JournalTradeCreate) -> dict[str, Any]:
    tid = analyst.log_trade(
        normalize_symbol(body.symbol),
        body.timeframe,
        body.trade_type,
        body.direction,
        entry=body.entry,
        stop=body.stop,
        target=body.target,
        rr_planned=body.rr_planned,
        wave=body.wave,
        result=body.result,
        emotion=body.emotion,
        notes=body.notes,
    )
    return {"id": tid, "ok": True}


@app.get("/api/settings")
def settings_get() -> dict[str, Any]:
    return read_settings()


@app.put("/api/settings/risk")
def settings_risk(body: RiskSettingsUpdate) -> dict[str, Any]:
    return update_risk_settings(body.model_dump(exclude_none=True))


@app.put("/api/settings/anthropic-key")
def settings_anthropic_key(body: AnthropicKeyUpdate) -> dict[str, Any]:
    if not body.api_key.strip():
        raise HTTPException(400, "Empty API key")
    write_env_key("ANTHROPIC_API_KEY", body.api_key.strip())
    return {"ok": True, "has_anthropic_key": True}


@app.get("/api/chat/history")
def chat_history(symbol: str | None = Query(None), limit: int = Query(30)) -> list[dict]:
    return get_chat_history(limit=limit, symbol=symbol)


@app.post("/api/chat")
def chat_post(body: ChatRequest):
    def generate():
        for chunk in stream_chat(
            body.message,
            body.symbol,
            body.timeframe,
            body.trade_type,
            include_chart=body.include_chart,
            quick_action=body.quick_action,
            with_books=body.with_books,
        ):
            if chunk == "[DONE]":
                yield "data: [DONE]\n\n"
            else:
                yield f"data: {chunk}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


if STATIC_DIR.exists():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def run_server(host: str | None = None, port: int | None = None) -> None:
    import uvicorn

    cfg = get_config()
    web_cfg = cfg.get("web", {})
    host = host or web_cfg.get("host", "127.0.0.1")
    port = port or int(web_cfg.get("port", 8780))
    uvicorn.run(app, host=host, port=port, log_level="info")
