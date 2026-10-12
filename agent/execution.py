"""Semi-automatic execution on BingX: propose -> human approval -> post-only bracket.

Nothing in this module sends an order on its own initiative. A proposal is created
(from the web form, the trade context, the verdict or the chat), validated against
hard limits, and only placed when the trader approves it in the web cockpit. The
background monitor only *reduces* exposure: it cancels stale or invalidated entry
orders and records fills/closes in the journal.

Hard limits live in `config.yaml` -> `execution:` and are enforced here, never in a
prompt. Live mode can only be enabled by editing config.yaml by hand.
"""

from __future__ import annotations

import json
import math
import secrets
import sqlite3
import threading
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from agent import analyst
from agent.config import get_config
from agent.exchange import PROTECTIVE_ORDER_TYPES, BingXClient, ExchangeError

ACTIVE_STATUSES = ("placed", "filled")
EventListener = Callable[[dict[str, Any]], None]


class ExecutionError(RuntimeError):
    """User-facing error (bad request, blocked by a limit, exchange failure)."""


class Exchange(Protocol):
    demo: bool

    def market_info(self, symbol: str) -> dict[str, float]: ...
    def book_top(self, symbol: str) -> tuple[float, float]: ...
    def round_price(self, symbol: str, price: float) -> float: ...
    def equity(self) -> tuple[float, float]: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def open_orders(self, symbol: str) -> list[dict[str, Any]]: ...
    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]: ...
    def closed_orders(self, symbol: str, since_ms: int) -> list[dict[str, Any]]: ...
    def prepare(self, symbol: str, leverage: int, margin_mode: str) -> list[str]: ...
    def place_bracket(self, **kwargs: Any) -> dict[str, Any]: ...
    def cancel_order(self, order_id: str, symbol: str) -> None: ...
    def close_position(self, symbol: str, side: str, qty: float) -> dict[str, Any]: ...


# --------------------------------------------------------------------------- limits


@dataclass(frozen=True)
class ExecutionLimits:
    mode: str = "demo"
    symbols: tuple[str, ...] = ("HYPEUSDT", "BTCUSDT")
    max_risk_usd: float = 6.0
    max_risk_pct: float = 2.0
    max_daily_loss_usd: float = 12.0
    consecutive_loss_stop: int = 2
    max_open_positions: int = 1
    leverage: int = 10
    max_leverage: int = 10
    min_rr: float = 2.0
    min_stop_pct: float = 0.15
    max_entry_distance_pct: float = 3.0
    proposal_ttl_sec: int = 180
    order_ttl_min: int = 120
    margin_mode: str = "isolated"
    tp_order: str = "limit"
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    monitor_interval_sec: int = 15

    @property
    def is_live(self) -> bool:
        return self.mode == "live"


def load_limits(cfg: dict[str, Any] | None = None) -> ExecutionLimits:
    raw = dict((cfg or get_config()).get("execution") or {})
    defaults = ExecutionLimits()
    values: dict[str, Any] = {}
    for name in ExecutionLimits.__dataclass_fields__:
        if name not in raw:
            continue
        default = getattr(defaults, name)
        value = raw[name]
        if isinstance(default, tuple):
            value = tuple(str(s).upper() for s in value)
        elif isinstance(default, bool):
            value = bool(value)
        elif isinstance(default, int):
            value = int(value)
        elif isinstance(default, float):
            value = float(value)
        else:
            value = str(value).lower()
        values[name] = value
    limits = ExecutionLimits(**values)
    if limits.mode not in ("demo", "live"):
        raise ExecutionError(f"execution.mode must be demo or live, got {limits.mode!r}")
    if limits.tp_order not in ("limit", "market"):
        raise ExecutionError("execution.tp_order must be limit or market")
    return limits


# --------------------------------------------------------------------------- sizing


@dataclass
class OrderPlan:
    symbol: str
    side: str
    entry: float
    stop: float
    take_profit: float
    qty: float
    leverage: int
    risk_usd: float
    notional_usd: float
    margin_usd: float
    stop_pct: float
    rr: float
    fees_if_stop_usd: float
    fees_if_target_usd: float

    @property
    def fees_if_stop_r(self) -> float:
        return self.fees_if_stop_usd / self.risk_usd if self.risk_usd else 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["fees_if_stop_r"] = round(self.fees_if_stop_r, 3)
        return data


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    # Small epsilon so 0.3 / 0.1 style float noise does not drop a whole step.
    steps = math.floor(value / step + 1e-9)
    decimals = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return round(steps * step, decimals)


def build_plan(
    *,
    symbol: str,
    side: str,
    entry: float,
    stop: float,
    take_profit: float,
    risk_budget_usd: float,
    leverage: int,
    amount_step: float,
    maker_fee: float,
    taker_fee: float,
    tp_order: str,
) -> OrderPlan:
    """Size from risk: qty = risk budget / stop distance, floored to the lot step.

    Margin never decides size (margen != riesgo). Fees: entry rests as maker; the stop
    is a stop-market (taker); the TP is maker only when tp_order == 'limit'."""
    for name, value in (("entry", entry), ("stop", stop), ("take_profit", take_profit)):
        if not (isinstance(value, (int, float)) and math.isfinite(value) and value > 0):
            raise ExecutionError(f"{name} must be a positive number")
    if side not in ("long", "short"):
        raise ExecutionError("side must be long or short")
    distance = abs(entry - stop)
    if distance == 0:
        raise ExecutionError("stop cannot equal entry")
    qty = _floor_to_step(max(risk_budget_usd, 0.0) / distance, amount_step)
    notional = qty * entry
    tp_fee = maker_fee if tp_order == "limit" else taker_fee
    return OrderPlan(
        symbol=symbol,
        side=side,
        entry=entry,
        stop=stop,
        take_profit=take_profit,
        qty=qty,
        leverage=leverage,
        risk_usd=round(qty * distance, 4),
        notional_usd=round(notional, 4),
        margin_usd=round(notional / leverage, 4) if leverage else notional,
        stop_pct=round(distance / entry * 100, 4),
        rr=round(abs(take_profit - entry) / distance, 3),
        fees_if_stop_usd=round(notional * maker_fee + qty * stop * taker_fee, 4),
        fees_if_target_usd=round(notional * maker_fee + qty * take_profit * tp_fee, 4),
    )


@dataclass
class MarketState:
    bid: float
    ask: float
    equity: float
    free: float
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    active_entries: int = 0
    daily_loss_usd: float = 0.0
    consecutive_losses: int = 0
    halted: bool = False
    min_qty: float = 0.0
    min_cost: float = 0.0


def risk_budget(limits: ExecutionLimits, equity: float) -> float:
    return min(limits.max_risk_usd, equity * limits.max_risk_pct / 100.0)


def check_plan(plan: OrderPlan, limits: ExecutionLimits, state: MarketState) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Any error blocks placement."""
    errors: list[str] = []
    warnings: list[str] = []

    if state.halted:
        errors.append("Kill switch activo: ejecución bloqueada hasta reanudar.")
    if plan.symbol not in limits.symbols:
        errors.append(f"{plan.symbol} no está en execution.symbols {list(limits.symbols)}.")
    if plan.leverage > limits.max_leverage:
        errors.append(f"Leverage x{plan.leverage} supera el máximo x{limits.max_leverage}.")

    if plan.side == "long" and not (plan.stop < plan.entry < plan.take_profit):
        errors.append("LONG requiere SL < entry < TP.")
    if plan.side == "short" and not (plan.take_profit < plan.entry < plan.stop):
        errors.append("SHORT requiere TP < entry < SL.")

    if plan.stop_pct < limits.min_stop_pct:
        errors.append(
            f"SL a {plan.stop_pct:.2f}% del entry: menor al mínimo {limits.min_stop_pct}% "
            "(ruido/spread te saca)."
        )
    liq_pct = 100.0 / plan.leverage if plan.leverage else 100.0
    if plan.stop_pct >= liq_pct * 0.5:
        errors.append(
            f"SL a {plan.stop_pct:.2f}% con x{plan.leverage}: la liquidación está a ~{liq_pct:.1f}%. "
            "Bajá leverage o acercá el SL."
        )
    if plan.rr < limits.min_rr:
        errors.append(f"R:R {plan.rr:.2f} menor al mínimo {limits.min_rr}.")

    budget = risk_budget(limits, state.equity)
    if plan.qty <= 0 or plan.qty < state.min_qty:
        errors.append(
            f"Cantidad {plan.qty} menor al mínimo del exchange ({state.min_qty}) con riesgo ${budget:.2f}."
        )
    if plan.notional_usd < state.min_cost:
        errors.append(f"Notional ${plan.notional_usd:.2f} menor al mínimo del exchange ${state.min_cost}.")
    if plan.risk_usd > budget + 1e-6:
        errors.append(f"Riesgo ${plan.risk_usd:.2f} supera el tope ${budget:.2f}.")
    if plan.margin_usd > state.free * 0.95:
        errors.append(f"Margen ${plan.margin_usd:.2f} supera el disponible ${state.free:.2f}.")

    if state.daily_loss_usd >= limits.max_daily_loss_usd:
        errors.append(
            f"Pérdida del día ${state.daily_loss_usd:.2f} alcanzó el tope ${limits.max_daily_loss_usd}. "
            "Se terminó el día."
        )
    if state.consecutive_losses >= limits.consecutive_loss_stop:
        errors.append(
            f"{state.consecutive_losses} pérdidas seguidas hoy: stop anti-revenge hasta mañana (UTC)."
        )
    exposure = len(state.open_positions) + state.active_entries
    if exposure >= limits.max_open_positions:
        errors.append(
            f"Ya hay {exposure} posición/orden activa (máximo {limits.max_open_positions})."
        )

    # Post-only: a buy at/above the ask (or sell at/below the bid) would take liquidity
    # and BingX rejects it. Catch it before sending.
    if plan.side == "long" and plan.entry >= state.ask:
        errors.append(f"Entry {plan.entry} >= ask {state.ask}: sería taker y PostOnly la rechaza.")
    if plan.side == "short" and plan.entry <= state.bid:
        errors.append(f"Entry {plan.entry} <= bid {state.bid}: sería taker y PostOnly la rechaza.")
    mid = (state.bid + state.ask) / 2
    distance_pct = abs(plan.entry - mid) / mid * 100 if mid else 0.0
    if distance_pct > limits.max_entry_distance_pct:
        errors.append(
            f"Entry a {distance_pct:.2f}% del precio: más lejos que {limits.max_entry_distance_pct}%. "
            "¿Contexto desactualizado?"
        )

    if plan.fees_if_stop_r > 0.1:
        warnings.append(
            f"Comisiones si salta el SL: {plan.fees_if_stop_r:.2f}R. SL muy ajustado para el costo."
        )
    if plan.risk_usd < budget * 0.8 and plan.qty > 0:
        warnings.append(f"Riesgo real ${plan.risk_usd:.2f} (redondeo al lote mínimo).")
    if limits.is_live:
        warnings.append("MODO LIVE: dinero real.")
    return errors, warnings


# --------------------------------------------------------------------------- storage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def init_execution_db() -> None:
    analyst.init_sessions_db()
    with closing(sqlite3.connect(analyst._sessions_db())) as conn, conn:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                mode TEXT NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                timeframe TEXT NOT NULL,
                source TEXT NOT NULL,
                rationale TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL,
                plan_json TEXT NOT NULL,
                errors_json TEXT NOT NULL DEFAULT '[]',
                warnings_json TEXT NOT NULL DEFAULT '[]',
                client_order_id TEXT,
                exchange_order_id TEXT,
                placed_at TEXT,
                filled_at TEXT,
                filled_qty REAL,
                fill_price REAL,
                closed_at TEXT,
                exit_price REAL,
                rr_real REAL,
                journal_trade_id INTEGER,
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS execution_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(analyst._sessions_db())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["plan"] = json.loads(data.pop("plan_json"))
    data["errors"] = json.loads(data.pop("errors_json"))
    data["warnings"] = json.loads(data.pop("warnings_json"))
    return data


def get_record(record_id: int) -> dict[str, Any] | None:
    init_execution_db()
    with closing(_connect()) as conn:
        row = conn.execute("SELECT * FROM execution_orders WHERE id = ?", (record_id,)).fetchone()
    return _row_to_dict(row) if row else None


def list_records(limit: int = 20, statuses: tuple[str, ...] | None = None) -> list[dict[str, Any]]:
    init_execution_db()
    query = "SELECT * FROM execution_orders"
    params: list[Any] = []
    if statuses:
        query += f" WHERE status IN ({','.join('?' * len(statuses))})"
        params.extend(statuses)
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with closing(_connect()) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def _update(record_id: int, **fields: Any) -> None:
    fields["updated_at"] = _iso(_now())
    cols = ", ".join(f"{k} = ?" for k in fields)
    with closing(_connect()) as conn, conn:
        conn.execute(f"UPDATE execution_orders SET {cols} WHERE id = ?", (*fields.values(), record_id))


def _get_state(key: str) -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT value FROM execution_state WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_state(key: str, value: str) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO execution_state (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def consecutive_losses_today() -> int:
    """Leading run of losses among today's (UTC) closed journal trades, newest first."""
    analyst.init_sessions_db()
    today = _now().date().isoformat()
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT result FROM trades WHERE closed_at IS NOT NULL AND substr(closed_at, 1, 10) = ? "
            "ORDER BY closed_at DESC",
            (today,),
        ).fetchall()
    streak = 0
    for row in rows:
        if row["result"] != "loss":
            break
        streak += 1
    return streak


# --------------------------------------------------------------------------- service


class ExecutionService:
    """Owns the exchange client, the approval flow and the background monitor."""

    _instance: "ExecutionService | None" = None

    def __init__(
        self,
        limits: ExecutionLimits | None = None,
        exchange_factory: Callable[[bool], Exchange] | None = None,
    ) -> None:
        self._limits_override = limits
        self._exchange_factory = exchange_factory or (lambda demo: BingXClient.from_env(demo=demo))
        self._exchange: Exchange | None = None
        self._lock = threading.RLock()
        self._listeners: list[EventListener] = []
        self._monitor_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.last_monitor_error: str | None = None
        init_execution_db()

    @classmethod
    def instance(cls) -> "ExecutionService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    # --- plumbing ---------------------------------------------------------

    @property
    def limits(self) -> ExecutionLimits:
        return self._limits_override or load_limits()

    def exchange(self) -> Exchange:
        if self._exchange is None:
            self._exchange = self._exchange_factory(not self.limits.is_live)
        return self._exchange

    def reset_exchange(self) -> None:
        """Drop the cached client (after new keys or a mode change)."""
        with self._lock:
            self._exchange = None

    def add_listener(self, fn: EventListener) -> None:
        self._listeners.append(fn)

    def _emit(self, kind: str, payload: dict[str, Any]) -> None:
        event = {"type": "execution", "kind": kind, "payload": payload, "ts": _iso(_now())}
        for fn in self._listeners:
            try:
                fn(event)
            except Exception:
                pass

    def is_halted(self) -> bool:
        return _get_state("halted") == "1"

    def _daily_loss(self, equity: float) -> float:
        key = f"day_start_equity:{self.limits.mode}:{_now().date().isoformat()}"
        start = _get_state(key)
        if start is None:
            _set_state(key, str(equity))
            return 0.0
        return max(0.0, float(start) - equity)

    def market_state(self, symbol: str) -> MarketState:
        ex = self.exchange()
        bid, ask = ex.book_top(symbol)
        equity, free = ex.equity()
        info = ex.market_info(symbol)
        return MarketState(
            bid=bid,
            ask=ask,
            equity=equity,
            free=free,
            open_positions=ex.positions(),
            active_entries=len(list_records(limit=50, statuses=("placed",))),
            daily_loss_usd=self._daily_loss(equity),
            consecutive_losses=consecutive_losses_today(),
            halted=self.is_halted(),
            min_qty=info["min_qty"],
            min_cost=info["min_cost"],
        )

    # --- proposals --------------------------------------------------------

    def propose(
        self,
        *,
        symbol: str,
        side: str,
        entry: float,
        stop: float,
        take_profit: float,
        timeframe: str = "15m",
        source: str = "manual",
        rationale: str = "",
    ) -> dict[str, Any]:
        """Validate and store a proposal. Never touches the exchange's order API."""
        limits = self.limits
        symbol = symbol.upper().strip()
        side = side.lower().strip()
        if symbol not in limits.symbols:
            raise ExecutionError(f"{symbol} no está habilitado para ejecutar ({list(limits.symbols)}).")
        try:
            ex = self.exchange()
            info = ex.market_info(symbol)
            entry, stop, take_profit = (ex.round_price(symbol, p) for p in (entry, stop, take_profit))
            state = self.market_state(symbol)
        except ExchangeError as exc:
            raise ExecutionError(str(exc)) from exc
        plan = build_plan(
            symbol=symbol,
            side=side,
            entry=entry,
            stop=stop,
            take_profit=take_profit,
            risk_budget_usd=risk_budget(limits, state.equity),
            leverage=limits.leverage,
            amount_step=info["amount_step"],
            maker_fee=limits.maker_fee,
            taker_fee=limits.taker_fee,
            tp_order=limits.tp_order,
        )
        errors, warnings = check_plan(plan, limits, state)
        now = _now()
        status = "blocked" if errors else "pending"
        with closing(_connect()) as conn, conn:
            cursor = conn.execute(
                """
                INSERT INTO execution_orders
                (created_at, updated_at, expires_at, mode, symbol, side, timeframe, source,
                 rationale, status, plan_json, errors_json, warnings_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _iso(now),
                    _iso(now),
                    _iso(now + timedelta(seconds=limits.proposal_ttl_sec)),
                    limits.mode,
                    symbol,
                    side,
                    timeframe,
                    source,
                    rationale[:2000],
                    status,
                    json.dumps(plan.to_dict()),
                    json.dumps(errors, ensure_ascii=False),
                    json.dumps(warnings, ensure_ascii=False),
                ),
            )
            record_id = cursor.lastrowid
        record = get_record(record_id)
        record["market"] = {"bid": state.bid, "ask": state.ask, "equity": state.equity}
        self._emit("proposal", record)
        return record

    def reject(self, record_id: int) -> dict[str, Any]:
        with self._lock:
            record = self._require(record_id)
            if record["status"] not in ("pending", "blocked"):
                raise ExecutionError(f"Propuesta #{record_id} está {record['status']}, no se puede rechazar.")
            _update(record_id, status="rejected")
        return get_record(record_id)

    def approve(self, record_id: int, confirm: str | None = None) -> dict[str, Any]:
        """Human approval: re-check every limit with fresh data, then place the bracket."""
        limits = self.limits
        with self._lock:
            record = self._require(record_id)
            if record["status"] != "pending":
                raise ExecutionError(f"Propuesta #{record_id} está {record['status']}, no pendiente.")
            if record["mode"] != limits.mode:
                _update(record_id, status="expired", note="modo cambió desde la propuesta")
                raise ExecutionError("El modo demo/live cambió desde la propuesta. Proponé de nuevo.")
            if datetime.fromisoformat(record["expires_at"]) < _now():
                _update(record_id, status="expired")
                raise ExecutionError(f"Propuesta #{record_id} expiró. Proponé de nuevo con precio actual.")
            if limits.is_live and (confirm or "").strip().upper() != "LIVE":
                raise ExecutionError('Modo LIVE: escribí "LIVE" para confirmar.')

            plan = OrderPlan(**{k: v for k, v in record["plan"].items() if k != "fees_if_stop_r"})
            try:
                state = self.market_state(plan.symbol)
            except ExchangeError as exc:
                raise ExecutionError(str(exc)) from exc
            errors, _ = check_plan(plan, limits, state)
            if errors:
                _update(record_id, status="failed", errors_json=json.dumps(errors, ensure_ascii=False))
                self._emit("failed", get_record(record_id))
                raise ExecutionError("Bloqueada al re-chequear: " + " | ".join(errors))

            client_order_id = f"th{record_id}x{secrets.token_hex(4)}"
            _update(record_id, status="approved", client_order_id=client_order_id)
            ex = self.exchange()
            try:
                prep_warnings = ex.prepare(plan.symbol, plan.leverage, limits.margin_mode)
                order = ex.place_bracket(
                    symbol=plan.symbol,
                    side=plan.side,
                    qty=plan.qty,
                    entry=plan.entry,
                    stop=plan.stop,
                    take_profit=plan.take_profit,
                    client_order_id=client_order_id,
                    tp_order=limits.tp_order,
                )
            except ExchangeError as exc:
                _update(record_id, status="failed", note=str(exc)[:500])
                self._emit("failed", get_record(record_id))
                raise ExecutionError(f"BingX rechazó la orden: {exc}") from exc

            note = "; ".join(prep_warnings)
            _update(
                record_id,
                status="placed",
                exchange_order_id=str(order.get("id") or ""),
                placed_at=_iso(_now()),
                note=note,
            )
        record = get_record(record_id)
        self._emit("placed", record)
        self.ensure_monitor()
        return record

    def cancel(self, record_id: int, reason: str = "cancelada por el trader") -> dict[str, Any]:
        with self._lock:
            record = self._require(record_id)
            if record["status"] != "placed":
                raise ExecutionError(f"Orden #{record_id} está {record['status']}, nada que cancelar.")
            try:
                self.exchange().cancel_order(record["exchange_order_id"], record["symbol"])
            except ExchangeError as exc:
                raise ExecutionError(str(exc)) from exc
            _update(record_id, status="cancelled", note=reason)
        record = get_record(record_id)
        self._emit("cancelled", record)
        return record

    def close_position(self, symbol: str, confirm: str | None = None) -> dict[str, Any]:
        """Market reduce-only close. Taker fee; explicit trader action only."""
        if self.limits.is_live and (confirm or "").strip().upper() != "LIVE":
            raise ExecutionError('Modo LIVE: escribí "LIVE" para confirmar el cierre.')
        symbol = symbol.upper()
        with self._lock:
            ex = self.exchange()
            try:
                positions = [p for p in ex.positions() if p["symbol"] == symbol]
                if not positions:
                    raise ExecutionError(f"No hay posición abierta en {symbol}.")
                results = [ex.close_position(symbol, p["side"], p["qty"]) for p in positions]
            except ExchangeError as exc:
                raise ExecutionError(str(exc)) from exc
        self._emit("close_requested", {"symbol": symbol})
        return {"ok": True, "orders": [r.get("id") for r in results]}

    def halt(self) -> dict[str, Any]:
        """Kill switch: block new orders and cancel resting *entry* orders.

        Exchange-side SL/TP of open positions are left alone on purpose: cancelling
        them would leave a position naked."""
        _set_state("halted", "1")
        cancelled, failed = [], []
        for record in list_records(limit=50, statuses=("placed",)):
            try:
                self.cancel(record["id"], reason="kill switch")
                cancelled.append(record["id"])
            except ExecutionError as exc:
                failed.append({"id": record["id"], "error": str(exc)})
        self._emit("halted", {"cancelled": cancelled, "failed": failed})
        return {"halted": True, "cancelled": cancelled, "failed": failed}

    def resume(self) -> dict[str, Any]:
        _set_state("halted", "0")
        self._emit("resumed", {})
        return {"halted": False}

    def _require(self, record_id: int) -> dict[str, Any]:
        record = get_record(record_id)
        if record is None:
            raise ExecutionError(f"Propuesta #{record_id} no existe.")
        return record

    # --- status & monitor -------------------------------------------------

    def status(self) -> dict[str, Any]:
        limits = self.limits
        data: dict[str, Any] = {
            "mode": limits.mode,
            "limits": {**asdict(limits), "symbols": list(limits.symbols)},
            "halted": self.is_halted(),
            "consecutive_losses": consecutive_losses_today(),
            "records": list_records(limit=20),
            "connected": False,
            "monitor_error": self.last_monitor_error,
        }
        try:
            ex = self.exchange()
            equity, free = ex.equity()
            data.update(
                connected=True,
                equity=equity,
                free=free,
                daily_loss_usd=self._daily_loss(equity),
                risk_budget_usd=round(risk_budget(limits, equity), 2),
                positions=ex.positions(),
            )
        except (ExchangeError, ExecutionError) as exc:
            data["error"] = str(exc)
        return data

    def sync(self) -> list[dict[str, Any]]:
        """Reconcile active records with the exchange. Returns the events produced."""
        events: list[dict[str, Any]] = []
        with self._lock:
            active = list_records(limit=50, statuses=ACTIVE_STATUSES)
            if not active:
                return events
            ex = self.exchange()
            positions = ex.positions()
            for record in active:
                try:
                    if record["status"] == "placed":
                        event = self._sync_placed(ex, record, positions)
                    else:
                        event = self._sync_filled(ex, record, positions)
                except ExchangeError as exc:
                    event = {"kind": "sync_error", "id": record["id"], "error": str(exc)}
                if event:
                    events.append(event)
        for event in events:
            self._emit(event["kind"], event)
        return events

    def _sync_placed(self, ex: Exchange, record: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
        order = ex.fetch_order(record["exchange_order_id"], record["symbol"])
        filled = float(order.get("filled") or 0)
        status = order.get("status")
        plan = record["plan"]
        if status == "closed" or (status == "canceled" and filled > 0):
            return self._mark_filled(record, order, filled)
        if status in ("canceled", "rejected", "expired"):
            _update(record["id"], status="cancelled", note=f"exchange: {status} (PostOnly cruzó o vencida)")
            return {"kind": "cancelled", "id": record["id"], "reason": status}
        if filled > 0:
            return None  # partially filled: let it work, SL/TP cover the filled part

        reason = None
        placed_at = datetime.fromisoformat(record["placed_at"])
        if _now() - placed_at > timedelta(minutes=self.limits.order_ttl_min):
            reason = f"sin fill en {self.limits.order_ttl_min} min"
        else:
            bid, ask = ex.book_top(record["symbol"])
            if plan["side"] == "long" and bid <= plan["stop"]:
                reason = "precio tocó el SL antes del fill: setup invalidado"
            elif plan["side"] == "short" and ask >= plan["stop"]:
                reason = "precio tocó el SL antes del fill: setup invalidado"
            elif plan["side"] == "long" and bid >= plan["take_profit"]:
                reason = "precio llegó al TP sin fill: trade perdido, no perseguir"
            elif plan["side"] == "short" and ask <= plan["take_profit"]:
                reason = "precio llegó al TP sin fill: trade perdido, no perseguir"
        if reason:
            ex.cancel_order(record["exchange_order_id"], record["symbol"])
            _update(record["id"], status="cancelled", note=reason)
            return {"kind": "cancelled", "id": record["id"], "reason": reason}
        return None

    def _mark_filled(self, record: dict[str, Any], order: dict[str, Any], filled: float) -> dict[str, Any]:
        plan = record["plan"]
        fill_price = float(order.get("average") or order.get("price") or plan["entry"])
        journal_id = None
        try:
            journal_id = analyst.log_trade(
                record["symbol"],
                record["timeframe"],
                "scalp",
                plan["side"],
                entry=fill_price,
                stop=plan["stop"],
                target=plan["take_profit"],
                rr_planned=plan["rr"],
                notes=(
                    f"BingX {record['mode']} #{record['id']} order {record['exchange_order_id']} "
                    f"qty {filled} riesgo ${plan['risk_usd']}. {record['rationale']}"
                ).strip(),
            )
        except Exception:
            pass
        _update(
            record["id"],
            status="filled",
            filled_at=_iso(_now()),
            filled_qty=filled,
            fill_price=fill_price,
            journal_trade_id=journal_id,
        )
        return {"kind": "filled", "id": record["id"], "fill_price": fill_price, "qty": filled}

    def _sync_filled(self, ex: Exchange, record: dict[str, Any], positions: list[dict[str, Any]]) -> dict[str, Any] | None:
        side = record["plan"]["side"]
        if any(p["symbol"] == record["symbol"] and p.get("side") == side for p in positions):
            return None
        # Position gone: SL or TP hit (or closed by hand). Find the exit fill.
        plan = record["plan"]
        exit_price = None
        filled_ms = int(datetime.fromisoformat(record["filled_at"]).timestamp() * 1000)
        exit_side = "sell" if side == "long" else "buy"
        try:
            closes = [
                o
                for o in ex.closed_orders(record["symbol"], filled_ms)
                if o.get("side") == exit_side and float(o.get("filled") or 0) > 0
                and o.get("id") != record["exchange_order_id"]
            ]
            if closes:
                last = max(closes, key=lambda o: o.get("timestamp") or 0)
                exit_price = float(last.get("average") or last.get("price"))
        except ExchangeError:
            pass

        # Leftover SL/TP orders on a flat symbol are orphans: remove them.
        for order in ex.open_orders(record["symbol"]):
            if order.get("reduceOnly") or str(order.get("type", "")).lower() in PROTECTIVE_ORDER_TYPES:
                try:
                    ex.cancel_order(order["id"], record["symbol"])
                except ExchangeError:
                    pass

        rr_real = None
        result = None
        entry = record["fill_price"] or plan["entry"]
        if exit_price is not None:
            direction = 1 if side == "long" else -1
            rr_real = round((exit_price - entry) * direction / abs(entry - plan["stop"]), 3)
            result = "win" if rr_real > 0.05 else "loss" if rr_real < -0.05 else "breakeven"
            if record["journal_trade_id"]:
                try:
                    analyst.close_trade(record["journal_trade_id"], result, rr_real, "")
                except Exception:
                    pass
        _update(
            record["id"],
            status="closed",
            closed_at=_iso(_now()),
            exit_price=exit_price,
            rr_real=rr_real,
            note="" if exit_price is not None else "no se encontró el precio de salida: cerrá el trade en el journal",
        )
        return {"kind": "closed", "id": record["id"], "exit_price": exit_price, "rr_real": rr_real, "result": result}

    def ensure_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_event.clear()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, name="execution-monitor", daemon=True)
        self._monitor_thread.start()

    def stop_monitor(self) -> None:
        self._stop_event.set()

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                if list_records(limit=1, statuses=ACTIVE_STATUSES):
                    self.sync()
                    self.last_monitor_error = None
            except Exception as exc:  # keep the monitor alive on any failure
                self.last_monitor_error = f"{type(exc).__name__}: {exc}"
            self._stop_event.wait(max(5, self.limits.monitor_interval_sec))


# --------------------------------------------------------------------------- suggestions


def suggest_from_context(symbol: str) -> dict[str, Any]:
    """Prefill from context/<SYMBOL>.yaml: POI mid as entry, invalidation as SL, first target as TP."""
    from agent.context import get_context

    ctx = get_context(symbol.upper())
    if ctx is None:
        raise ExecutionError(f"No hay contexto para {symbol}.")
    if ctx.poi_low is None or ctx.poi_high is None or ctx.invalidation is None or not ctx.targets:
        raise ExecutionError("El contexto necesita POI low/high, invalidación y al menos un target.")
    # The side comes from the levels, not from `bias`: an invalidation below the POI is a
    # long setup whatever the bias field says (they drift apart when only one is edited).
    if ctx.invalidation < ctx.poi_low:
        side = "long"
    elif ctx.invalidation > ctx.poi_high:
        side = "short"
    else:
        raise ExecutionError("La invalidación está dentro del POI: no se puede deducir el lado.")
    rationale = ctx.wave
    if ctx.bias in ("long", "short") and ctx.bias != side:
        rationale = f"[ojo: bias del contexto = {ctx.bias}, niveles = {side}] {ctx.wave}"
    return {
        "symbol": ctx.symbol,
        "side": side,
        "entry": round((ctx.poi_low + ctx.poi_high) / 2, 6),
        "stop": ctx.invalidation,
        "take_profit": ctx.targets[0],
        "source": "context",
        "rationale": rationale,
    }


def suggest_from_verdict(verdict: dict[str, Any], symbol: str) -> dict[str, Any]:
    """Prefill from the setup engine's verdict levels."""
    levels = verdict.get("levels") or {}
    direction = verdict.get("direction")
    if levels.get("context_stale") or verdict.get("context_stale"):
        raise ExecutionError("Contexto desactualizado: rehacé el conteo antes de proponer desde el veredicto.")
    if direction not in ("long", "short") or levels.get("entry") is None or levels.get("stop") is None or levels.get("target") is None:
        raise ExecutionError("El veredicto actual no tiene niveles operables (dirección/entry/SL/TP).")
    return {
        "symbol": symbol.upper(),
        "side": direction,
        "entry": levels["entry"],
        "stop": levels["stop"],
        "take_profit": levels["target"],
        "source": "verdict",
        "rationale": f"{verdict.get('grade', '')} {verdict.get('summary', '')}".strip(),
    }
