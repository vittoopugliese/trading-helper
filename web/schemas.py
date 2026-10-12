"""Pydantic models for Trading Cockpit Web API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ContextUpdate(BaseModel):
    bias: str | None = None
    wave: str | None = None
    trade_type: str | None = None
    poi_low: float | None = None
    poi_high: float | None = None
    invalidation: float | None = None
    targets: list[float] | None = None
    notes: str | None = None
    account_size: float | None = None
    risk_pct: float | None = None


class WatcherStartRequest(BaseModel):
    symbol: str = "HYPEUSDT"
    timeframe: str = "15m"
    scalp: bool = True
    bias: Literal["long", "short", "both"] = "both"
    interval_sec: int = Field(default=30, ge=10, le=300)


class RiskSettingsUpdate(BaseModel):
    default_account_size: float | None = None
    max_risk_usd: float | None = None
    leverage: int | None = None


class AnthropicKeyUpdate(BaseModel):
    api_key: str


class ChatRequest(BaseModel):
    message: str
    symbol: str = "HYPEUSDT"
    timeframe: str = "15m"
    trade_type: str = "scalp"
    include_chart: bool = True
    quick_action: str | None = None
    with_books: bool = False  # True = suma la biblioteca de libros al contexto del chat


class JournalTradeCreate(BaseModel):
    symbol: str
    timeframe: str
    direction: Literal["long", "short"]
    trade_type: str = "scalp"
    entry: float | None = None
    stop: float | None = None
    target: float | None = None
    rr_planned: float | None = None
    wave: str = ""
    result: Literal["open", "win", "loss", "breakeven", "missed"] = "open"
    emotion: str = ""
    notes: str = ""
    of_pattern: str = ""
    elliott_alineado: bool | None = None


class JournalTradeClose(BaseModel):
    result: Literal["win", "loss", "breakeven"]
    rr_real: float | None = None
    emotion: str = ""


class ExecutionProposal(BaseModel):
    symbol: str = "HYPEUSDT"
    side: Literal["long", "short"]
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    take_profit: float = Field(gt=0)
    timeframe: str = "15m"
    source: Literal["manual", "context", "verdict", "chat"] = "manual"
    rationale: str = Field(default="", max_length=2000)


class ExecutionApprove(BaseModel):
    confirm: str | None = None  # "LIVE" required when execution.mode == live


class ExecutionClose(BaseModel):
    confirm: str | None = None


class BingXKeysUpdate(BaseModel):
    api_key: str
    api_secret: str
