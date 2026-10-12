"""Execution endpoints: propose / approve / cancel / kill switch on BingX.

Every route requires a localhost Host header (blocks DNS rebinding) and the custom
`X-Cockpit: 1` header. A custom header forces a CORS preflight, which the server
only allows for its own origin, so other websites open in the browser cannot fire
these requests at 127.0.0.1.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from agent.execution import (
    ExecutionError,
    ExecutionService,
    get_record,
    suggest_from_context,
    suggest_from_verdict,
)
from web.schemas import (
    BingXKeysUpdate,
    ExecutionApprove,
    ExecutionClose,
    ExecutionProposal,
)
from web.services import analyze_for_api, normalize_symbol, write_env_key

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


def require_cockpit(request: Request) -> None:
    host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]")
    if host not in LOCAL_HOSTS:
        raise HTTPException(403, "Execution API only on localhost")
    if request.headers.get("x-cockpit") != "1":
        raise HTTPException(403, "Missing X-Cockpit header")


router = APIRouter(prefix="/api/execution", dependencies=[Depends(require_cockpit)])


def _service() -> ExecutionService:
    return ExecutionService.instance()


def _guard(fn, *args, **kwargs) -> Any:
    try:
        return fn(*args, **kwargs)
    except ExecutionError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/status")
def execution_status() -> dict[str, Any]:
    return _service().status()


@router.get("/suggest/{symbol}")
def execution_suggest(
    symbol: str,
    source: str = Query("context", pattern="^(context|verdict)$"),
    timeframe: str = Query("15m"),
    trade_type: str = Query("scalp"),
) -> dict[str, Any]:
    sym = normalize_symbol(symbol)
    if source == "context":
        return _guard(suggest_from_context, sym)
    analysis = analyze_for_api(sym, timeframe, trade_type, persist=False)
    return _guard(suggest_from_verdict, analysis.get("verdict") or {}, sym)


@router.post("/proposals")
def execution_propose(body: ExecutionProposal) -> dict[str, Any]:
    return _guard(
        _service().propose,
        symbol=normalize_symbol(body.symbol),
        side=body.side,
        entry=body.entry,
        stop=body.stop,
        take_profit=body.take_profit,
        timeframe=body.timeframe,
        source=body.source,
        rationale=body.rationale,
    )


@router.get("/proposals/{record_id}")
def execution_get(record_id: int) -> dict[str, Any]:
    record = get_record(record_id)
    if record is None:
        raise HTTPException(404, "Not found")
    return record


@router.post("/proposals/{record_id}/approve")
def execution_approve(record_id: int, body: ExecutionApprove) -> dict[str, Any]:
    return _guard(_service().approve, record_id, body.confirm)


@router.post("/proposals/{record_id}/reject")
def execution_reject(record_id: int) -> dict[str, Any]:
    return _guard(_service().reject, record_id)


@router.post("/orders/{record_id}/cancel")
def execution_cancel(record_id: int) -> dict[str, Any]:
    return _guard(_service().cancel, record_id)


@router.post("/positions/{symbol}/close")
def execution_close(symbol: str, body: ExecutionClose) -> dict[str, Any]:
    return _guard(_service().close_position, normalize_symbol(symbol), body.confirm)


@router.post("/sync")
def execution_sync() -> dict[str, Any]:
    return {"events": _guard(_service().sync)}


@router.post("/halt")
def execution_halt() -> dict[str, Any]:
    return _service().halt()


@router.post("/resume")
def execution_resume() -> dict[str, Any]:
    return _service().resume()


@router.put("/keys")
def execution_keys(body: BingXKeysUpdate) -> dict[str, Any]:
    key, secret = body.api_key.strip(), body.api_secret.strip()
    if not key or not secret:
        raise HTTPException(400, "Empty key or secret")
    write_env_key("BINGX_API_KEY", key)
    write_env_key("BINGX_API_SECRET", secret)
    _service().reset_exchange()
    return {"ok": True, "has_bingx_keys": True}
