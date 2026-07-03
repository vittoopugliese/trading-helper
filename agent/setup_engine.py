"""A+ setup scoring engine — Elliott context drives bias, order flow confirms at POI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.config import get_config
from agent.context import TradeContext, get_context
from agent import metrics


@dataclass
class ConditionResult:
    name: str
    passed: bool
    weight: float
    score: float
    detail: str
    critical: bool = False


@dataclass
class SetupVerdict:
    grade: str  # A+, B, C, NO_TRADE
    score: float
    direction: str  # long, short, none
    conditions: list[ConditionResult] = field(default_factory=list)
    levels: dict[str, float | None] = field(default_factory=dict)
    position_size: dict[str, float | None] = field(default_factory=dict)
    trade_type: str = "swing"
    summary: str = ""
    at_poi: bool = False
    of_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "score": round(self.score, 4),
            "direction": self.direction,
            "trade_type": self.trade_type,
            "summary": self.summary,
            "levels": self.levels,
            "position_size": self.position_size,
            "at_poi": self.at_poi,
            "of_patterns": self.of_patterns,
            "conditions": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "weight": c.weight,
                    "score": c.score,
                    "detail": c.detail,
                    "critical": c.critical,
                }
                for c in self.conditions
            ],
        }


def _infer_direction(snapshot: metrics.MarketSnapshot, ctx: TradeContext | None) -> str:
    if ctx and ctx.bias in ("long", "short"):
        return ctx.bias

    trend = snapshot.structure.get("trend", "neutral")
    flow = snapshot.order_flow.get("bias", "mixed")

    if trend == "bullish" and flow in ("bullish", "mixed"):
        return "long"
    if trend == "bearish" and flow in ("bearish", "mixed"):
        return "short"
    if flow == "bullish":
        return "long"
    if flow == "bearish":
        return "short"
    return "none"


def _check_trend_alignment(snapshot: metrics.MarketSnapshot, direction: str) -> ConditionResult:
    trend = snapshot.structure.get("trend", "neutral")
    passed = (direction == "long" and trend == "bullish") or (direction == "short" and trend == "bearish")
    return ConditionResult(
        name="trend_alignment",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else 0.0,
        detail=f"Structure trend={trend}, trade direction={direction}",
    )


def _check_delta_cvd(snapshot: metrics.MarketSnapshot, direction: str) -> ConditionResult:
    flow = snapshot.order_flow
    bias = flow.get("bias", "mixed")
    passed = (direction == "long" and bias == "bullish") or (direction == "short" and bias == "bearish")
    source = flow.get("source", "klines")
    return ConditionResult(
        name="delta_cvd",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else (0.5 if bias == "mixed" else 0.0),
        detail=(
            f"CVD bias={bias} ({source}), delta={flow.get('recent_delta_sum') or flow.get('recent_delta_sum_5')}, "
            f"buy_ratio={flow.get('buy_ratio', 'n/a')}"
        ),
        critical=True,
    )


def _check_volume_spike(snapshot: metrics.MarketSnapshot) -> ConditionResult:
    ind = snapshot.indicators
    ratio = ind.get("volume_ratio", 1.0)
    passed = bool(ind.get("volume_spike"))
    return ConditionResult(
        name="volume_spike",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else min(ratio / 1.5, 0.8),
        detail=f"Volume ratio vs MA20 = {ratio}x",
    )


def _check_oi_confirmation(snapshot: metrics.MarketSnapshot, direction: str) -> ConditionResult:
    deriv = snapshot.derivatives
    if "error" in deriv:
        return ConditionResult(
            name="oi_confirmation",
            passed=False,
            weight=0.0,
            score=0.5,
            detail=f"OI data unavailable: {deriv['error']}",
        )

    oi_change = deriv.get("oi_change_1h")
    if oi_change is None:
        return ConditionResult(
            name="oi_confirmation",
            passed=False,
            weight=0.0,
            score=0.5,
            detail="OI change data unavailable",
        )

    passed = oi_change > 0
    return ConditionResult(
        name="oi_confirmation",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else 0.3,
        detail=f"OI change 1h={oi_change}, OI={deriv.get('open_interest')}",
    )


def _check_funding_neutral(snapshot: metrics.MarketSnapshot) -> ConditionResult:
    deriv = snapshot.derivatives
    if "error" in deriv:
        return ConditionResult(
            name="funding_neutral",
            passed=True,
            weight=0.0,
            score=0.5,
            detail="Funding data unavailable - neutral assumption",
        )

    extreme = deriv.get("funding_extreme", False)
    rate = deriv.get("funding_rate", 0.0)
    passed = not extreme
    return ConditionResult(
        name="funding_neutral",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else 0.2,
        detail=f"Funding rate={rate}",
    )


def _check_structure_swing(snapshot: metrics.MarketSnapshot) -> ConditionResult:
    swings = snapshot.structure.get("recent_swings", [])
    passed = len(swings) >= 2
    return ConditionResult(
        name="structure_swing",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else 0.3,
        detail=f"Recent swings detected: {len(swings)}",
    )


def _check_risk_reward(levels: dict[str, float | None]) -> ConditionResult:
    cfg = get_config()
    rr = levels.get("risk_reward") or 0.0
    min_rr = cfg["scoring"]["min_rr"]
    passed = rr >= min_rr
    return ConditionResult(
        name="risk_reward",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else min(rr / min_rr, 0.9) if min_rr else 0.0,
        detail=f"R:R = {rr} (min {min_rr}) [{levels.get('source', 'atr')}]",
        critical=True,
    )


def _check_price_in_poi(snapshot: metrics.MarketSnapshot, ctx: TradeContext | None) -> ConditionResult:
    cfg = get_config()
    proximity = cfg["thresholds"].get("poi_proximity_pct", 0.003)
    tolerance = cfg["thresholds"].get("poi_tolerance_pct", 0.001)
    price = snapshot.price

    if ctx is None or not ctx.has_poi():
        return ConditionResult(
            name="price_in_poi",
            passed=False,
            weight=0.0,
            score=0.3,
            detail="No POI defined in context — define context/SYMBOL.yaml",
        )

    in_poi = ctx.price_in_poi(price, tolerance)
    near = ctx.price_near_poi(price, proximity)
    passed = in_poi or near
    fib = ctx.nearest_fib(price)
    detail = f"Price={price}, POI=[{ctx.poi_low}, {ctx.poi_high}], in_poi={in_poi}, near={near}"
    if fib:
        detail += f", nearest fib={fib}"
    return ConditionResult(
        name="price_in_poi",
        passed=passed,
        weight=0.0,
        score=1.0 if in_poi else (0.7 if near else 0.0),
        detail=detail,
    )


def _check_invalidation_respected(snapshot: metrics.MarketSnapshot, ctx: TradeContext | None) -> ConditionResult:
    if ctx is None or ctx.invalidation is None:
        return ConditionResult(
            name="invalidation_respected",
            passed=True,
            weight=0.0,
            score=0.5,
            detail="No invalidation level defined",
        )

    passed = ctx.invalidation_respected(snapshot.price)
    return ConditionResult(
        name="invalidation_respected",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else 0.0,
        detail=f"Price={snapshot.price}, invalidation={ctx.invalidation}, bias={ctx.bias}",
        critical=True,
    )


def _check_of_confirmation_at_poi(
    snapshot: metrics.MarketSnapshot,
    ctx: TradeContext | None,
    direction: str,
) -> ConditionResult:
    cfg = get_config()
    tolerance = cfg["thresholds"].get("poi_tolerance_pct", 0.001)
    in_poi = False
    if ctx and ctx.has_poi():
        in_poi = ctx.price_in_poi(snapshot.price, tolerance)

    pattern_info = metrics.detect_of_pattern_at_poi(snapshot.order_flow, direction, in_poi)
    passed = pattern_info["confirmed"]
    patterns = pattern_info.get("patterns", [])
    return ConditionResult(
        name="of_confirmation_at_poi",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else (0.4 if patterns else 0.0),
        detail=f"Patterns={patterns or 'none'}, in_poi={in_poi}",
        critical=in_poi,
    )


def _check_cvd_divergence(snapshot: metrics.MarketSnapshot, direction: str, ctx: TradeContext | None) -> ConditionResult:
    divergence = snapshot.order_flow.get("cvd_divergence")
    wave = (ctx.wave.lower() if ctx and ctx.wave else "")

    if "5" in wave and direction == "long" and divergence == "bearish":
        return ConditionResult(
            name="cvd_divergence",
            passed=False,
            weight=0.0,
            score=0.0,
            detail="Bearish CVD divergence on wave 5 long — distribution risk",
        )
    if "5" in wave and direction == "short" and divergence == "bullish":
        return ConditionResult(
            name="cvd_divergence",
            passed=False,
            weight=0.0,
            score=0.0,
            detail="Bullish CVD divergence on wave 5 short — squeeze risk",
        )

    favorable = (direction == "long" and divergence == "bullish") or (
        direction == "short" and divergence == "bearish"
    )
    neutral = divergence is None
    passed = favorable or neutral
    return ConditionResult(
        name="cvd_divergence",
        passed=passed,
        weight=0.0,
        score=1.0 if favorable else (0.7 if neutral else 0.2),
        detail=f"CVD divergence={divergence or 'none'}",
    )


def evaluate_setup(
    snapshot: metrics.MarketSnapshot,
    trade_context: TradeContext | None = None,
) -> SetupVerdict:
    """Score snapshot against A+ checklist with Elliott context."""
    cfg = get_config()
    trade_type = snapshot.trade_type
    weights = cfg["weights"].get(trade_type, cfg["weights"]["swing"])
    threshold = cfg["scoring"]["a_plus_threshold"]

    ctx = trade_context or get_context(snapshot.symbol)
    direction = _infer_direction(snapshot, ctx)

    if direction == "none":
        return SetupVerdict(
            grade="NO_TRADE",
            score=0.0,
            direction="none",
            trade_type=trade_type,
            summary="No clear directional bias. Define bias in context/SYMBOL.yaml or wait for structure+flow.",
        )

    from agent import market_data

    df = market_data.load_klines_df(snapshot.symbol, snapshot.timeframe)
    levels = metrics.suggest_levels(df, direction, ctx)
    entry = levels.get("entry")
    stop = levels.get("stop")
    pos_size = metrics.compute_position_size(
        float(entry) if entry else snapshot.price,
        float(stop) if stop else None,
        ctx.account_size if ctx else None,
        ctx.risk_pct if ctx else None,
    )

    tolerance = cfg["thresholds"].get("poi_tolerance_pct", 0.001)
    at_poi = bool(ctx and ctx.has_poi() and ctx.price_in_poi(snapshot.price, tolerance))
    pattern_info = metrics.detect_of_pattern_at_poi(snapshot.order_flow, direction, at_poi)

    checks = [
        _check_trend_alignment(snapshot, direction),
        _check_delta_cvd(snapshot, direction),
        _check_volume_spike(snapshot),
        _check_oi_confirmation(snapshot, direction),
        _check_funding_neutral(snapshot),
        _check_structure_swing(snapshot),
        _check_risk_reward(levels),
        _check_price_in_poi(snapshot, ctx),
        _check_invalidation_respected(snapshot, ctx),
        _check_of_confirmation_at_poi(snapshot, ctx, direction),
        _check_cvd_divergence(snapshot, direction, ctx),
    ]

    weighted_score = 0.0
    critical_failed = False

    for check in checks:
        w = weights.get(check.name, 0.0)
        check.weight = w
        check.score = check.score * w
        weighted_score += check.score
        if check.critical and not check.passed:
            critical_failed = True

    if critical_failed:
        grade = "NO_TRADE"
    elif weighted_score >= threshold:
        grade = "A+"
    elif weighted_score >= threshold - 0.15:
        grade = "B"
    else:
        grade = "NO_TRADE"

    passed_count = sum(1 for c in checks if c.passed)
    wave_info = f" | {ctx.wave}" if ctx and ctx.wave else ""
    poi_info = " @ POI" if at_poi else ""
    summary = (
        f"{grade} - {direction.upper()} {trade_type} on {snapshot.symbol} {snapshot.timeframe}{poi_info}{wave_info}. "
        f"Score {weighted_score:.0%} ({passed_count}/{len(checks)} conditions met)."
    )

    return SetupVerdict(
        grade=grade,
        score=weighted_score,
        direction=direction,
        conditions=checks,
        levels=levels,
        position_size=pos_size,
        trade_type=trade_type,
        summary=summary,
        at_poi=at_poi,
        of_patterns=pattern_info.get("patterns", []),
    )
