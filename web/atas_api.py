"""ATAS bridge ingest + footprint / liquidation / signal-ledger read endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from agent import atas_feed, liquidations, signal_ledger
from web.services import normalize_symbol

router = APIRouter(prefix="/api")

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


def _require_bridge(request: Request) -> None:
    # Only the local ATAS indicator may write. The custom header forces a CORS preflight,
    # so a web page open in the browser cannot post fake footprint data to 127.0.0.1.
    host = request.client.host if request.client else ""
    if host not in _LOOPBACK or request.headers.get("x-atas-bridge") != "1":
        raise HTTPException(403, "solo el indicador ATAS local puede enviar datos")


@router.post("/atas/ingest")
async def atas_ingest(request: Request) -> dict[str, Any]:
    _require_bridge(request)
    try:
        payload = await request.json()
    except ValueError as exc:
        raise HTTPException(400, "JSON invalido") from exc
    try:
        return {"ok": True, **atas_feed.ingest(payload)}
    except atas_feed.IngestError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/atas/status")
def atas_status() -> dict[str, Any]:
    feeds = atas_feed.feeds()
    for feed in feeds:
        feed["age_sec"] = atas_feed._age_sec(feed["last_seen"])
    return {"feeds": feeds, "liquidations": liquidations.LiquidationService.instance().status}


@router.get("/atas/snapshot/{symbol}")
def atas_snapshot(symbol: str, timeframe: str = Query("5m")) -> dict[str, Any]:
    return atas_feed.best_snapshot(normalize_symbol(symbol), timeframe)


@router.get("/atas/bars/{symbol}")
def atas_bars(symbol: str, timeframe: str = Query("5m"), limit: int = Query(60, ge=1, le=500)) -> list[dict[str, Any]]:
    bars = atas_feed.load_bars(normalize_symbol(symbol), timeframe, limit)
    out = []
    prev = None
    for bar in bars:
        feats = atas_feed.bar_features(bar, prev)
        prev = bar
        out.append({k: bar[k] for k in ("ts", "closed", "open", "high", "low", "close", "volume", "delta",
                                         "max_delta", "min_delta", "oi", "poc", "vah", "val")}
                   | {"signals": atas_feed.long_short_signals(feats)})
    return out


@router.get("/liquidations/{symbol}")
def liquidation_summary(symbol: str) -> dict[str, Any]:
    return liquidations.summary(normalize_symbol(symbol))


@router.get("/signals/stats")
def signals_stats(symbol: str | None = None) -> dict[str, Any]:
    return signal_ledger.stats(normalize_symbol(symbol) if symbol else None)


@router.get("/signals/recent")
def signals_recent(limit: int = Query(30, ge=1, le=200)) -> list[dict[str, Any]]:
    return signal_ledger.recent(limit)
