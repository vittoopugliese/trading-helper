"""Single source of truth for "may this be traded?": levels, risk and daily guards.

Every surface (setup engine, watcher, scalp alerts, cockpit, chat, MCP) asks this module
instead of re-deriving its own rules. The numbers come from config.yaml -> `risk` and
`scoring`; the notes describe them, they do not override them (see
notes/setup-rules/datos-requeridos.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.config import get_config

POLICY_VERSION = "2026-10-12.1"


def risk_config() -> dict[str, Any]:
    risk = get_config().get("risk", {})
    return {
        "account": float(risk.get("default_account_size", 300)),
        "max_risk_pct": float(risk.get("max_risk_pct", 2.0)),
        "max_risk_usd": float(risk.get("max_risk_usd", 0) or 0) or None,
        "leverage": float(risk.get("leverage", 1)),
        "margin_max": float(risk.get("margin_max", 0) or 0) or None,
        "max_total_exposure_pct": float(risk.get("max_total_exposure_pct", 0) or 0) or None,
        "daily_stop_losses": int(risk.get("daily_stop_losses", 0) or 0),
        "cost_bps_per_side": float(risk.get("cost_bps_per_side", 7.0)),
    }


def min_rr() -> float:
    return float(get_config()["scoring"].get("min_rr", 2.0))


# --------------------------------------------------------------------------- levels


def level_problems(direction: str, entry: float | None, stop: float | None, target: float | None,
                   required_rr: float | None = None) -> list[str]:
    """Why these levels are not tradable (empty list = OK). Geometry first, then R:R."""
    if direction not in ("long", "short"):
        return ["sin direccion"]
    if entry is None or stop is None or target is None:
        return ["faltan niveles (entry/SL/TP)"]
    if direction == "long" and not (stop < entry < target):
        return [f"niveles invertidos para long (SL {stop} / entry {entry} / TP {target})"]
    if direction == "short" and not (target < entry < stop):
        return [f"niveles invertidos para short (TP {target} / entry {entry} / SL {stop})"]
    rr = abs(target - entry) / abs(entry - stop)
    need = min_rr() if required_rr is None else required_rr
    if rr < need:
        return [f"R:R {rr:.2f} < minimo {need}"]
    return []


def risk_reward(entry: float, stop: float, target: float) -> float:
    risk = abs(entry - stop)
    return round(abs(target - entry) / risk, 2) if risk > 0 else 0.0


# --------------------------------------------------------------------------- journal guards


def _journal_rows(limit: int = 50) -> list[dict[str, Any]] | None:
    try:
        from agent import analyst

        return analyst.get_recent_trades(limit=limit)
    except Exception:
        return None


def daily_guard(rows: list[dict[str, Any]] | None = None, now: datetime | None = None) -> dict[str, Any]:
    """Consecutive losses today (local date) and open trades, read from the journal.

    If the journal cannot be read the state is `unknown`, never "all clear".
    """
    cfg = risk_config()
    rows = _journal_rows() if rows is None else rows
    if rows is None:
        return {"known": False, "blocked": False, "open_trades": None, "losses_streak_today": None,
                "detail": "journal no disponible: racha y exposicion desconocidas"}
    now = now or datetime.now().astimezone()
    today = now.date()

    def _local_date(ts: str | None):
        if not ts:
            return None
        try:
            return datetime.fromisoformat(ts).astimezone(now.tzinfo).date()
        except ValueError:
            return None

    closed_today = [r for r in rows if r.get("result") in ("win", "loss", "breakeven")
                    and _local_date(r.get("closed_at") or r.get("created_at")) == today]
    closed_today.sort(key=lambda r: r.get("closed_at") or r.get("created_at") or "", reverse=True)
    streak = 0
    for r in closed_today:
        if r["result"] != "loss":
            break
        streak += 1
    open_trades = sum(1 for r in rows if r.get("result") == "open")
    limit = cfg["daily_stop_losses"]
    blocked = bool(limit and streak >= limit)
    return {
        "known": True,
        "blocked": blocked,
        "losses_streak_today": streak,
        "open_trades": open_trades,
        "detail": f"{streak} perdidas seguidas hoy (tope {limit})" + (" -> STOP por hoy" if blocked else ""),
    }


# --------------------------------------------------------------------------- sizing


def size_position(entry: float, stop: float | None, *, account: float | None = None,
                  risk_pct: float | None = None, open_trades: int | None = None) -> dict[str, Any]:
    """Position size that respects every configured cap, with the effective risk recomputed.

    risk $ = min(account x pct, max_risk_usd, remaining exposure budget); qty = risk / (SL
    distance + round-trip costs); then margin is capped by margin_max and the account. When a
    cap shrinks the size, the effective risk is recomputed from the final qty.
    """
    cfg = risk_config()
    account = float(account or cfg["account"])
    pct = min(float(risk_pct or cfg["max_risk_pct"]), cfg["max_risk_pct"])
    out: dict[str, Any] = {"account_size": account, "risk_pct": pct, "leverage": cfg["leverage"],
                           "capped_by": [], "risk_amount": None, "position_size": None,
                           "notional": None, "margin": None, "policy_version": POLICY_VERSION}
    if stop is None or entry <= 0 or entry == stop:
        out["blocked_reason"] = "sin SL valido"
        return out

    budget = account * pct / 100
    if cfg["max_risk_usd"] and cfg["max_risk_usd"] < budget:
        budget = cfg["max_risk_usd"]
        out["capped_by"].append(f"max_risk_usd {cfg['max_risk_usd']}")
    if cfg["max_total_exposure_pct"] and open_trades:
        # Open journal trades carry no size: assume each uses the full per-trade cap.
        per_trade = cfg["max_risk_usd"] or account * cfg["max_risk_pct"] / 100
        remaining = account * cfg["max_total_exposure_pct"] / 100 - open_trades * per_trade
        if remaining < budget:
            budget = max(remaining, 0.0)
            out["capped_by"].append(f"exposicion total {cfg['max_total_exposure_pct']}% con {open_trades} abierto(s)")
    if budget <= 0:
        out["blocked_reason"] = "sin presupuesto de riesgo (exposicion total agotada)"
        return out

    distance = abs(entry - stop)
    costs = entry * cfg["cost_bps_per_side"] * 2 / 10_000
    qty = budget / (distance + costs)
    margin_cap = min(cfg["margin_max"] or account, account)
    margin = qty * entry / cfg["leverage"]
    if margin > margin_cap:
        qty = margin_cap * cfg["leverage"] / entry
        out["capped_by"].append(f"margen max {margin_cap:g} a x{cfg['leverage']:g}")

    effective = qty * (distance + costs)
    out.update({
        "risk_amount": round(effective, 2),
        "risk_budget": round(budget, 2),
        "position_size": round(qty, 4),
        "notional": round(qty * entry, 2),
        "margin": round(qty * entry / cfg["leverage"], 2),
        "per_unit_risk": round(distance, 6),
        "costs_per_unit": round(costs, 6),
        "stop_pct": round(distance / entry * 100, 3),
    })
    return out


def describe() -> str:
    """Active policy as text for prompts (complete, never truncated)."""
    cfg = risk_config()
    return (
        f"POLITICA ACTIVA ({POLICY_VERSION}, config.yaml):\n"
        f"- Riesgo por trade: min({cfg['max_risk_pct']}% de la cuenta ${cfg['account']:g}, "
        f"${cfg['max_risk_usd'] or '-'}). Leverage x{cfg['leverage']:g}, margen max ${cfg['margin_max'] or cfg['account']:g}.\n"
        f"- Exposicion total max {cfg['max_total_exposure_pct'] or '-'}% (trades abiertos del journal cuentan con el tope completo).\n"
        f"- {cfg['daily_stop_losses']} perdidas seguidas hoy = no operar mas hoy.\n"
        f"- R:R minimo {min_rr()} medido con SL y TP del lado correcto del entry.\n"
        f"- Costos {cfg['cost_bps_per_side']} bps por lado incluidos en el sizing.\n"
        "- Una entrada solo es elegible si el veredicto dice eligible=true (sin blocked_reasons). "
        "Si hay blocked_reasons, se informan y NO se sugiere entrar.\n"
        "- La regla del 2% de Elder es teoria de referencia; el tope vigente es el de arriba."
    )
