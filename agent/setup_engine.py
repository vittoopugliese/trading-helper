"""A+ setup scoring engine — Elliott context drives bias, order flow confirms at POI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

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
    context_stale: bool = False
    context_drift_pct: float | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    eligible: bool = False
    blocked_reasons: list[str] = field(default_factory=list)
    policy_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "grade": self.grade,
            "context_stale": self.context_stale,
            "context_drift_pct": self.context_drift_pct,
            "score": round(self.score, 4),
            "direction": self.direction,
            "trade_type": self.trade_type,
            "summary": self.summary,
            "levels": self.levels,
            "position_size": self.position_size,
            "at_poi": self.at_poi,
            "of_patterns": self.of_patterns,
            "evidence": self.evidence,
            "eligible": self.eligible,
            "blocked_reasons": self.blocked_reasons,
            "policy_version": self.policy_version,
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


def _check_risk_reward(levels: dict[str, Any], direction: str) -> ConditionResult:
    """R:R only counts when SL and TP sit on the correct side of the entry for `direction`
    (an abs() R:R on inverted levels used to look positive)."""
    from agent import policy

    min_rr = policy.min_rr()
    problems = policy.level_problems(direction, levels.get("entry"), levels.get("stop"), levels.get("target"))
    rr = levels.get("risk_reward") or 0.0
    passed = not problems
    return ConditionResult(
        name="risk_reward",
        passed=passed,
        weight=0.0,
        score=1.0 if passed else (min(rr / min_rr, 0.9) if min_rr and "invertidos" not in " ".join(problems) else 0.0),
        detail=f"R:R = {rr} (min {min_rr}) [{levels.get('source', 'atr')}]" + (f" — {problems[0]}" if problems else ""),
        critical=True,
    )


_TF_SECONDS = {"m": 60, "h": 3600, "d": 86400, "w": 604800}


def _tf_seconds(tf: str) -> int:
    try:
        return int(tf[:-1]) * _TF_SECONDS[tf[-1]]
    except (KeyError, ValueError, IndexError):
        return 0


def _check_htf_alignment(snapshot: metrics.MarketSnapshot, direction: str) -> ConditionResult:
    """Checklist star: trade WITH the higher-timeframe trend. Uses every context TF above the
    execution TF. All of them against = counter-trend (blocks A+); no data = unknown."""
    cfg = get_config()
    exec_sec = _tf_seconds(snapshot.timeframe)
    tf_by_label = cfg.get("timeframes", {})
    trends = {}
    for label, summary in (snapshot.context_timeframes or {}).items():
        tf = tf_by_label.get(label, label)
        if _tf_seconds(tf) > exec_sec:
            trends[tf] = (summary.get("structure") or {}).get("trend")
    want = "bullish" if direction == "long" else "bearish"
    against = "bearish" if direction == "long" else "bullish"
    with_us = [tf for tf, t in trends.items() if t == want]
    vs_us = [tf for tf, t in trends.items() if t == against]
    if not trends:
        return ConditionResult("htf_alignment", True, 0.0, 0.5, "Sin temporalidades superiores en el snapshot (desconocido)")
    counter = bool(vs_us) and not with_us
    detail = f"HTF a favor={with_us or 'ninguna'}, en contra={vs_us or 'ninguna'}"
    return ConditionResult("htf_alignment", not counter, 0.0, 0.0 if counter else 1.0,
                           detail + (" — CONTRA TENDENCIA HTF" if counter else ""))


def _invalidated_since(symbol: str, dctx: TradeContext | None) -> str | None:
    """Was the scenario's invalidation already hit after the context was written?

    A thesis broken last Tuesday stays broken even if price came back to the POI. Uses 1h
    candles (300 cached = 12 days, more than context_max_age_days). None = not hit / unknown.
    """
    if dctx is None or dctx.invalidation is None or not dctx.as_of:
        return None
    from agent import market_data

    try:
        as_of = pd.Timestamp(dctx.as_of)
        as_of = as_of.tz_localize("UTC") if as_of.tzinfo is None else as_of.tz_convert("UTC")
        df = market_data.load_klines_df(symbol, "1h")
        if df is None or df.empty or "open_time" not in df:
            return None
        times = pd.to_datetime(df["open_time"], utc=True)
        after = df[times >= as_of.floor("h")]
        if after.empty:
            return None
        if dctx.bias == "long":
            hit = after[after["low"] <= dctx.invalidation]
        else:
            hit = after[after["high"] >= dctx.invalidation]
        if hit.empty:
            return None
        return str(pd.to_datetime(hit["open_time"].iloc[0], utc=True))
    except Exception:
        return None


def _directional_context(ctx: TradeContext | None, direction: str) -> TradeContext | None:
    """The context seen from `direction`: its own POI / invalidation / targets, or None."""
    if ctx is None or direction not in ("long", "short"):
        return None
    scenario = ctx.scenario(direction)
    if scenario is None:
        return None
    view = TradeContext.from_dict(ctx.to_dict(), ctx.symbol)
    view.bias = direction
    view.poi_low, view.poi_high = scenario["poi_low"], scenario["poi_high"]
    view.invalidation = scenario["invalidation"]
    view.targets = list(scenario["targets"])
    view.as_of = ctx.as_of
    return view


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


def _check_atas_footprint(snapshot: metrics.MarketSnapshot, direction: str) -> tuple[ConditionResult, dict[str, Any]]:
    from agent import atas_feed

    conf = atas_feed.footprint_confirmation(snapshot.atas or {}, direction)
    if not conf["available"]:
        reason = (snapshot.atas or {}).get("reason") or "bridge ATAS sin datos"
        return ConditionResult("atas_footprint", False, 0.0, 0.0, f"ATAS no disponible: {reason}"), conf
    tf_used = snapshot.atas.get("tf")
    bar = f" (vela {conf['bar'][11:16]} UTC)" if conf.get("bar") else ""
    detail = f"ATAS {tf_used}{bar}: a favor={conf['signals'] or 'nada'}, en contra={conf['against'] or 'nada'}"
    return ConditionResult("atas_footprint", conf["confirmed"], 0.0, 1.0 if conf["confirmed"] else 0.0, detail), conf


def _check_liquidation_flush(snapshot: metrics.MarketSnapshot, direction: str) -> tuple[ConditionResult, dict[str, Any]]:
    from agent import liquidations

    flush = liquidations.flush_confirmation(snapshot.liquidations or {}, direction)
    return ConditionResult("liquidation_flush", flush["flush"], 0.0, 1.0 if flush["flush"] else 0.0, flush["detail"]), flush


def evaluate_setup(
    snapshot: metrics.MarketSnapshot,
    trade_context: TradeContext | None = None,
) -> SetupVerdict:
    """Score snapshot against the A+ checklist and decide eligibility in ONE place.

    `grade` says how good the setup looks; `eligible` says whether an entry may be suggested.
    Every surface (watcher, cockpit, chat, scheduler) must read `eligible` and show
    `blocked_reasons` instead of re-deriving its own entry rule.
    """
    from agent import market_data, policy

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
            blocked_reasons=["sin direccion"],
            policy_version=policy.POLICY_VERSION,
        )

    blocked: list[str] = []
    hard_block = False
    if ctx is not None:
        issues = ctx.validate()
        if issues:
            blocked += [f"contexto: {i}" for i in issues]
            hard_block = True
        if ctx.is_expired():
            blocked.append(f"contexto vencido: editado hace {ctx.age_days():.0f} dias "
                           f"(max {cfg['thresholds'].get('context_max_age_days', 7)})")
            hard_block = True
    dctx = _directional_context(ctx, direction)
    if ctx is not None and ctx.has_poi() and dctx is None:
        blocked.append(f"el contexto no tiene un escenario {direction} coherente")
        hard_block = True
    invalidated_at = _invalidated_since(snapshot.symbol, dctx)
    if invalidated_at:
        blocked.append(f"tesis {direction} invalidada desde {invalidated_at} (toco {dctx.invalidation}); rehacer el contexto")
        hard_block = True

    df = market_data.load_klines_df(snapshot.symbol, snapshot.timeframe)
    levels = metrics.suggest_levels(df, direction, ctx)
    entry = levels.get("entry")
    stop = levels.get("stop")
    guard = policy.daily_guard()
    pos_size = metrics.compute_position_size(
        float(entry) if entry else snapshot.price,
        float(stop) if stop else None,
        ctx.account_size if ctx else None,
        ctx.risk_pct if ctx else None,
        open_trades=guard.get("open_trades"),
    )
    if guard["blocked"]:
        blocked.append(guard["detail"])
        hard_block = True
    if pos_size.get("blocked_reason"):
        blocked.append(f"sizing: {pos_size['blocked_reason']}")
        hard_block = True

    tolerance = cfg["thresholds"].get("poi_tolerance_pct", 0.001)
    at_poi = bool(dctx and dctx.price_in_poi(snapshot.price, tolerance))
    pattern_info = metrics.detect_of_pattern_at_poi(snapshot.order_flow, direction, at_poi)

    poi_ctx = dctx or ctx
    context_stale = bool(poi_ctx and poi_ctx.has_poi() and poi_ctx.is_stale(snapshot.price))
    context_drift = poi_ctx.poi_drift_pct(snapshot.price) if poi_ctx and poi_ctx.has_poi() else None
    if context_stale:
        blocked.append(f"contexto desactualizado: precio a {(context_drift or 0):.1%} del POI {direction}")
        hard_block = True

    checks = [
        _check_trend_alignment(snapshot, direction),
        _check_htf_alignment(snapshot, direction),
        _check_delta_cvd(snapshot, direction),
        _check_volume_spike(snapshot),
        _check_oi_confirmation(snapshot, direction),
        _check_funding_neutral(snapshot),
        _check_structure_swing(snapshot),
        _check_risk_reward(levels, direction),
        _check_price_in_poi(snapshot, dctx),
        _check_invalidation_respected(snapshot, dctx),
        _check_of_confirmation_at_poi(snapshot, dctx, direction),
        _check_cvd_divergence(snapshot, direction, dctx),
    ]
    # Weight 0 on purpose: new evidence is shown and recorded in the signal ledger, but it
    # does not move the score until forward results show it helps (see notes/research).
    atas_check, atas_conf = _check_atas_footprint(snapshot, direction)
    liq_check, liq_flush = _check_liquidation_flush(snapshot, direction)
    checks += [atas_check, liq_check]

    weighted_score = 0.0
    for check in checks:
        w = weights.get(check.name, 0.0)
        check.weight = w
        check.score = check.score * w
        weighted_score += check.score
        if check.critical and not check.passed:
            blocked.append(f"{check.name}: {check.detail}")
            hard_block = True

    if weighted_score >= threshold:
        grade = "A+"
    elif weighted_score >= threshold - 0.15:
        grade = "B"
    else:
        grade = "NO_TRADE"
    if hard_block:
        grade = "NO_TRADE"

    # Soft gates: the setup may look fine but is not an A+ entry.
    gate_note = ""
    htf = next(c for c in checks if c.name == "htf_alignment")
    if not htf.passed:
        blocked.append("contra tendencia HTF (checklist: operar a favor de la tendencia)")
        if grade == "A+":
            grade, gate_note = "B", " Contra tendencia HTF: baja a B."
    # Footprint gate (the 4th signal of the gatillo notes): with live ATAS data an entry needs
    # the footprint on its side. Without ATAS the grade stands but the alert says so.
    atas_cfg = cfg.get("atas") or {}
    if atas_conf["available"]:
        if atas_conf["contradicted"]:
            blocked.append("footprint ATAS en contra")
            if grade == "A+":
                grade, gate_note = "B", gate_note + " Footprint ATAS EN CONTRA: baja a B."
        elif atas_cfg.get("require_confirmation_for_a_plus", True) and at_poi and not atas_conf["confirmed"]:
            blocked.append("en POI sin gatillo footprint ATAS (absorcion / delta reversal / flip)")
            if grade == "A+":
                grade, gate_note = "B", gate_note + " En POI sin gatillo footprint ATAS: baja a B."
    if grade != "A+" and not blocked:
        blocked.append(f"score {weighted_score:.0%} < A+ ({threshold:.0%})")

    eligible = grade == "A+" and not blocked

    passed_count = sum(1 for c in checks if c.passed)
    wave_info = f" | {ctx.wave}" if ctx and ctx.wave else ""
    poi_info = " @ POI" if at_poi else ""
    block_info = f" BLOQUEADO: {blocked[0]}" + (f" (+{len(blocked) - 1})" if len(blocked) > 1 else "") if blocked else ""
    summary = (
        f"{grade} - {direction.upper()} {trade_type} on {snapshot.symbol} {snapshot.timeframe}{poi_info}{wave_info}. "
        f"Score {weighted_score:.0%} ({passed_count}/{len(checks)} conditions met).{gate_note}{block_info}"
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
        context_stale=context_stale,
        context_drift_pct=round(context_drift, 4) if context_drift is not None else None,
        evidence={"atas": atas_conf, "liquidations": liq_flush, "daily_guard": guard},
        eligible=eligible,
        blocked_reasons=blocked,
        policy_version=policy.POLICY_VERSION,
    )
