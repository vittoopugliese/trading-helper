"""Thin BingX perpetual-swap client (ccxt) used by the execution layer.

Only this module talks to the exchange's private API. `agent.execution` depends on
the small interface below, so tests can swap in a fake client.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv

# BingX attached SL/TP and reduce-only orders show up with these types.
PROTECTIVE_ORDER_TYPES = {
    "stop",
    "stop_market",
    "take_profit",
    "take_profit_market",
    "trigger_market",
    "trigger_limit",
    "trailing_stop_market",
}


class ExchangeError(RuntimeError):
    """Readable error raised for any exchange-side failure."""


def to_ccxt_symbol(symbol: str) -> str:
    """HYPEUSDT -> HYPE/USDT:USDT (USDT-margined perpetual)."""
    sym = symbol.upper().strip()
    if "/" in sym:
        return sym
    if not sym.endswith("USDT"):
        raise ExchangeError(f"Unsupported symbol {symbol}: only USDT perpetuals")
    return f"{sym[:-4]}/USDT:USDT"


def from_ccxt_symbol(symbol: str) -> str:
    """HYPE/USDT:USDT -> HYPEUSDT."""
    return symbol.split(":")[0].replace("/", "")


class BingXClient:
    """Wraps ccxt.bingx for USDT-M perpetuals. demo=True routes to the VST sandbox."""

    def __init__(self, api_key: str, secret: str, *, demo: bool) -> None:
        try:
            import ccxt
        except ImportError as exc:  # pragma: no cover - dependency guard
            raise ExchangeError("ccxt not installed: pip install ccxt") from exc
        self._ccxt = ccxt
        self.demo = demo
        self.ex = ccxt.bingx(
            {
                "apiKey": api_key,
                "secret": secret,
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            }
        )
        if demo:
            self.ex.set_sandbox_mode(True)
        self._markets_loaded = False
        self._hedged: bool | None = None

    @classmethod
    def from_env(cls, *, demo: bool) -> "BingXClient":
        load_dotenv(override=True)
        key = os.getenv("BINGX_API_KEY", "").strip()
        secret = os.getenv("BINGX_API_SECRET", "").strip()
        if not key or not secret:
            raise ExchangeError("BINGX_API_KEY / BINGX_API_SECRET not set. Configure en Settings.")
        return cls(key, secret, demo=demo)

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except self._ccxt.BaseError as exc:
            raise ExchangeError(f"{type(exc).__name__}: {exc}") from exc

    def _load(self) -> None:
        if not self._markets_loaded:
            self._call(self.ex.load_markets)
            self._markets_loaded = True

    # --- market data -------------------------------------------------------

    def market_info(self, symbol: str) -> dict[str, float]:
        self._load()
        m = self.ex.market(to_ccxt_symbol(symbol))
        return {
            "amount_step": float(m["precision"]["amount"]),
            "price_step": float(m["precision"]["price"]),
            "min_qty": float(m["limits"]["amount"]["min"] or 0),
            "min_cost": float(m["limits"]["cost"]["min"] or 0),
            "maker_fee": float(m.get("maker") or 0),
            "taker_fee": float(m.get("taker") or 0),
        }

    def book_top(self, symbol: str) -> tuple[float, float]:
        ob = self._call(self.ex.fetch_order_book, to_ccxt_symbol(symbol), 5)
        if not ob["bids"] or not ob["asks"]:
            raise ExchangeError(f"Empty order book for {symbol}")
        return float(ob["bids"][0][0]), float(ob["asks"][0][0])

    def round_price(self, symbol: str, price: float) -> float:
        self._load()
        return float(self.ex.price_to_precision(to_ccxt_symbol(symbol), price))

    # --- account -----------------------------------------------------------

    def equity(self) -> tuple[float, float]:
        """(total, free) margin balance. The VST sandbox reports VST instead of USDT."""
        bal = self._call(self.ex.fetch_balance, {"type": "swap"})
        for ccy in ("USDT", "VST"):
            if ccy in bal and bal[ccy].get("total") is not None:
                return float(bal[ccy]["total"] or 0), float(bal[ccy].get("free") or 0)
        return 0.0, 0.0

    def is_hedged(self) -> bool:
        if self._hedged is None:
            info = self._call(self.ex.fetch_position_mode)
            self._hedged = bool(info.get("hedged"))
        return self._hedged

    def positions(self) -> list[dict[str, Any]]:
        raw = self._call(self.ex.fetch_positions)
        out = []
        for p in raw:
            contracts = float(p.get("contracts") or 0)
            if contracts == 0:
                continue
            out.append(
                {
                    "symbol": from_ccxt_symbol(p["symbol"]),
                    "side": p.get("side"),
                    "qty": contracts,
                    "entry_price": p.get("entryPrice"),
                    "mark_price": p.get("markPrice"),
                    "unrealized_pnl": p.get("unrealizedPnl"),
                    "liquidation_price": p.get("liquidationPrice"),
                    "leverage": p.get("leverage"),
                }
            )
        return out

    def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return self._call(self.ex.fetch_open_orders, to_ccxt_symbol(symbol))

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self._call(self.ex.fetch_order, order_id, to_ccxt_symbol(symbol))

    def closed_orders(self, symbol: str, since_ms: int) -> list[dict[str, Any]]:
        return self._call(self.ex.fetch_closed_orders, to_ccxt_symbol(symbol), since_ms)

    # --- trading -----------------------------------------------------------

    def prepare(self, symbol: str, leverage: int, margin_mode: str) -> list[str]:
        """Set margin mode and leverage. Returns warnings instead of failing when the
        exchange refuses a change (e.g. margin mode locked by an open position)."""
        sym = to_ccxt_symbol(symbol)
        warnings: list[str] = []
        try:
            self._call(self.ex.set_margin_mode, margin_mode, sym)
        except ExchangeError as exc:
            warnings.append(f"margin mode no cambiado: {exc}")
        sides = ["LONG", "SHORT"] if self.is_hedged() else ["BOTH"]
        for side in sides:
            self._call(self.ex.set_leverage, leverage, sym, {"side": side})
        return warnings

    def place_bracket(
        self,
        *,
        symbol: str,
        side: str,
        qty: float,
        entry: float,
        stop: float,
        take_profit: float,
        client_order_id: str,
        tp_order: str,
    ) -> dict[str, Any]:
        """Post-only limit entry with exchange-side SL (stop-market on mark price) and
        TP attached. tp_order='limit' rests the TP at the target price (maker when it
        does not cross); 'market' uses TAKE_PROFIT_MARKET."""
        tp: dict[str, Any] = {"triggerPrice": take_profit, "workingType": "MARK_PRICE"}
        if tp_order == "limit":
            tp.update({"type": "TAKE_PROFIT", "price": take_profit})
        params: dict[str, Any] = {
            "postOnly": True,
            "clientOrderId": client_order_id,
            "stopLoss": {"triggerPrice": stop, "workingType": "MARK_PRICE", "type": "STOP_MARKET"},
            "takeProfit": tp,
        }
        if self.is_hedged():
            params["hedged"] = True
        order_side = "buy" if side == "long" else "sell"
        return self._call(
            self.ex.create_order, to_ccxt_symbol(symbol), "limit", order_side, qty, entry, params
        )

    def cancel_order(self, order_id: str, symbol: str) -> None:
        self._call(self.ex.cancel_order, order_id, to_ccxt_symbol(symbol))

    def close_position(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        """Market reduce-only close (taker). Only used on explicit user request."""
        params: dict[str, Any] = {"reduceOnly": True}
        if self.is_hedged():
            params["hedged"] = True
        order_side = "sell" if side == "long" else "buy"
        return self._call(
            self.ex.create_order, to_ccxt_symbol(symbol), "market", order_side, qty, None, params
        )
