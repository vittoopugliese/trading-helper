"""Blind review endpoints for the Elliott 1-2 backtest."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from agent import elliott_review

router = APIRouter(prefix="/api/elliott")


class ReviewDecision(BaseModel):
    candidate_id: str
    decision: str
    count_label: str | None = None
    confidence: int | None = Field(default=None, ge=1, le=3)
    note: str | None = None


@router.get("/review/next")
def review_next() -> dict[str, Any]:
    try:
        return elliott_review.next_candidate()
    except elliott_review.ReviewError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/review/decide")
def review_decide(body: ReviewDecision) -> dict[str, Any]:
    try:
        return elliott_review.decide(body.candidate_id, body.decision, body.count_label, body.confidence, body.note)
    except elliott_review.ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/review/stats")
def review_stats() -> dict[str, Any]:
    try:
        return elliott_review.stats()
    except elliott_review.ReviewError as exc:
        raise HTTPException(404, str(exc)) from exc
