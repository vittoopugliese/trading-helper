from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agent import analyst, execution
from agent.execution import (
    ExecutionError,
    ExecutionLimits,
    ExecutionService,
    MarketState,
    build_plan,
    check_plan,
)


class FakeExchange:
    """In-memory stand-in for BingXClient."""

    demo = True

    def __init__(self) -> None:
        self.bid, self.ask = 82.70, 82.72
        self.total, self.free = 300.0, 300.0
        self.positions_list: list[dict[str, Any]] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.open_list: list[dict[str, Any]] = []
        self.closed: list[dict[str, Any]] = []
        self.placed: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.closes: list[tuple[str, str, float]] = []

    def market_info(self, symbol: str) -> dict[str, float]:
        return {"amount_step": 0.0001, "price_step": 0.001, "min_qty": 0.0242, "min_cost": 2.0,
                "maker_fee": 0.0002, "taker_fee": 0.0005}

    def book_top(self, symbol: str) -> tuple[float, float]:
        return self.bid, self.ask

    def round_price(self, symbol: str, price: float) -> float:
        return round(price, 3)

    def equity(self) -> tuple[float, float]:
        return self.total, self.free

    def positions(self) -> list[dict[str, Any]]:
        return list(self.positions_list)

    def open_orders(self, symbol: str) -> list[dict[str, Any]]:
        return list(self.open_list)

    def fetch_order(self, order_id: str, symbol: str) -> dict[str, Any]:
        return self.orders[order_id]

    def closed_orders(self, symbol: str, since_ms: int) -> list[dict[str, Any]]:
        return list(self.closed)

    def prepare(self, symbol: str, leverage: int, margin_mode: str) -> list[str]:
        return []

    def place_bracket(self, **kwargs: Any) -> dict[str, Any]:
        order_id = f"o{len(self.placed) + 1}"
        self.placed.append(kwargs)
        self.orders[order_id] = {"id": order_id, "status": "open", "filled": 0.0}
        return {"id": order_id}

    def cancel_order(self, order_id: str, symbol: str) -> None:
        self.cancelled.append(order_id)
        if order_id in self.orders:
            self.orders[order_id]["status"] = "canceled"

    def close_position(self, symbol: str, side: str, qty: float) -> dict[str, Any]:
        self.closes.append((symbol, side, qty))
        return {"id": "close1"}


LIMITS = ExecutionLimits(mode="demo", max_risk_usd=6.0, max_risk_pct=2.0)


def long_plan(**overrides: Any) -> execution.OrderPlan:
    params = dict(
        symbol="HYPEUSDT", side="long", entry=82.0, stop=81.0, take_profit=84.5,
        risk_budget_usd=6.0, leverage=10, amount_step=0.0001,
        maker_fee=0.0002, taker_fee=0.0005, tp_order="limit",
    )
    params.update(overrides)
    return build_plan(**params)


def state(**overrides: Any) -> MarketState:
    base = dict(bid=82.70, ask=82.72, equity=300.0, free=300.0, min_qty=0.0242, min_cost=2.0)
    base.update(overrides)
    return MarketState(**base)


class SizingTests(unittest.TestCase):
    def test_size_comes_from_risk_not_margin(self) -> None:
        plan = long_plan()
        self.assertAlmostEqual(plan.qty, 6.0)  # $6 / $1 stop distance
        self.assertAlmostEqual(plan.risk_usd, 6.0)
        self.assertAlmostEqual(plan.notional_usd, 492.0)
        self.assertAlmostEqual(plan.margin_usd, 49.2)
        self.assertAlmostEqual(plan.rr, 2.5)

    def test_qty_floors_to_lot_step_so_risk_never_exceeds_budget(self) -> None:
        plan = long_plan(stop=81.3)  # 6 / 0.7 = 8.571428...
        self.assertAlmostEqual(plan.qty, 8.5714)
        self.assertLessEqual(plan.risk_usd, 6.0)

    def test_fees_maker_entry_taker_stop(self) -> None:
        plan = long_plan()
        expected = 6 * 82.0 * 0.0002 + 6 * 81.0 * 0.0005
        self.assertAlmostEqual(plan.fees_if_stop_usd, round(expected, 4))

    def test_rejects_stop_equal_entry(self) -> None:
        with self.assertRaises(ExecutionError):
            long_plan(stop=82.0)


class CheckPlanTests(unittest.TestCase):
    def test_valid_plan_passes(self) -> None:
        errors, _ = check_plan(long_plan(), LIMITS, state())
        self.assertEqual(errors, [])

    def test_wrong_geometry_blocked(self) -> None:
        plan = long_plan(take_profit=81.5, stop=80.0)
        errors, _ = check_plan(plan, LIMITS, state())
        self.assertTrue(any("SL < entry < TP" in e for e in errors))

    def test_post_only_crossing_blocked(self) -> None:
        plan = long_plan(entry=82.80, stop=81.8, take_profit=85.3)
        errors, _ = check_plan(plan, LIMITS, state())
        self.assertTrue(any("taker" in e for e in errors))

    def test_stop_near_liquidation_blocked(self) -> None:
        plan = long_plan(entry=82.0, stop=76.0, take_profit=95.0)  # 7.3% with x10
        errors, _ = check_plan(plan, LIMITS, state())
        self.assertTrue(any("liquidación" in e for e in errors))

    def test_low_rr_blocked(self) -> None:
        errors, _ = check_plan(long_plan(take_profit=83.0), LIMITS, state())
        self.assertTrue(any("R:R" in e for e in errors))

    def test_daily_loss_and_streak_and_halt_block(self) -> None:
        errors, _ = check_plan(long_plan(), LIMITS, state(daily_loss_usd=12.0, consecutive_losses=2, halted=True))
        joined = " ".join(errors)
        self.assertIn("Pérdida del día", joined)
        self.assertIn("pérdidas seguidas", joined)
        self.assertIn("Kill switch", joined)

    def test_one_position_at_a_time(self) -> None:
        errors, _ = check_plan(long_plan(), LIMITS, state(active_entries=1))
        self.assertTrue(any("activa" in e for e in errors))

    def test_risk_pct_of_equity_caps_budget(self) -> None:
        errors, _ = check_plan(long_plan(), LIMITS, state(equity=100.0))  # 2% of 100 = $2 < $6 plan
        self.assertTrue(any("supera el tope" in e for e in errors))

    def test_symbol_whitelist(self) -> None:
        errors, _ = check_plan(long_plan(symbol="DOGEUSDT"), LIMITS, state())
        self.assertTrue(any("execution.symbols" in e for e in errors))


class ServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.db = Path(self.tempdir.name) / "sessions.db"
        self.path_patch = patch("agent.analyst._sessions_db", return_value=self.db)
        self.path_patch.start()
        self.fake = FakeExchange()
        self.service = ExecutionService(limits=LIMITS, exchange_factory=lambda demo: self.fake)

    def tearDown(self) -> None:
        self.service.stop_monitor()
        self.path_patch.stop()
        self.tempdir.cleanup()

    def propose(self, **overrides: Any) -> dict[str, Any]:
        params = dict(symbol="HYPEUSDT", side="long", entry=82.0, stop=81.0, take_profit=84.5)
        params.update(overrides)
        return self.service.propose(**params)

    def test_propose_does_not_touch_orders(self) -> None:
        record = self.propose()
        self.assertEqual(record["status"], "pending")
        self.assertEqual(self.fake.placed, [])

    def test_blocked_proposal_cannot_be_approved(self) -> None:
        record = self.propose(entry=82.9, stop=81.9, take_profit=85.5)  # crosses ask
        self.assertEqual(record["status"], "blocked")
        with self.assertRaises(ExecutionError):
            self.service.approve(record["id"])
        self.assertEqual(self.fake.placed, [])

    def test_approve_places_post_only_bracket_once(self) -> None:
        record = self.propose()
        with patch.object(self.service, "ensure_monitor"):
            placed = self.service.approve(record["id"])
        self.assertEqual(placed["status"], "placed")
        self.assertEqual(len(self.fake.placed), 1)
        order = self.fake.placed[0]
        self.assertEqual((order["side"], order["qty"], order["entry"], order["stop"], order["take_profit"]),
                         ("long", 6.0, 82.0, 81.0, 84.5))
        with self.assertRaises(ExecutionError):
            self.service.approve(record["id"])
        self.assertEqual(len(self.fake.placed), 1)

    def test_live_mode_requires_typed_confirmation(self) -> None:
        self.service._limits_override = replace(LIMITS, mode="live")
        record = self.propose()
        with self.assertRaises(ExecutionError):
            self.service.approve(record["id"])
        with patch.object(self.service, "ensure_monitor"):
            self.assertEqual(self.service.approve(record["id"], confirm="LIVE")["status"], "placed")

    def test_expired_proposal_rejected(self) -> None:
        record = self.propose()
        past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        execution._update(record["id"], expires_at=past)
        with self.assertRaises(ExecutionError):
            self.service.approve(record["id"])
        self.assertEqual(execution.get_record(record["id"])["status"], "expired")

    def test_recheck_at_approval_uses_fresh_market(self) -> None:
        record = self.propose()
        self.fake.ask = 81.9  # price fell through the entry: limit would now cross
        with self.assertRaises(ExecutionError):
            self.service.approve(record["id"])
        self.assertEqual(execution.get_record(record["id"])["status"], "failed")
        self.assertEqual(self.fake.placed, [])

    def place(self) -> dict[str, Any]:
        record = self.propose()
        with patch.object(self.service, "ensure_monitor"):
            return self.service.approve(record["id"])

    def test_fill_then_stop_logs_and_closes_journal(self) -> None:
        record = self.place()
        order_id = record["exchange_order_id"]
        self.fake.orders[order_id].update(status="closed", filled=6.0, average=82.0)
        self.fake.positions_list = [{"symbol": "HYPEUSDT", "side": "long", "qty": 6.0}]
        events = self.service.sync()
        self.assertEqual(events[0]["kind"], "filled")
        filled = execution.get_record(record["id"])
        self.assertEqual(filled["status"], "filled")
        self.assertIsNotNone(filled["journal_trade_id"])

        self.fake.positions_list = []
        self.fake.closed = [{"id": "sl1", "side": "sell", "filled": 6.0, "average": 81.0, "timestamp": 2}]
        self.fake.open_list = [{"id": "tp1", "type": "take_profit", "reduceOnly": True}]
        events = self.service.sync()
        self.assertEqual(events[0]["kind"], "closed")
        self.assertEqual(events[0]["result"], "loss")
        self.assertAlmostEqual(events[0]["rr_real"], -1.0)
        self.assertIn("tp1", self.fake.cancelled)  # orphan TP removed
        trades = analyst.get_recent_trades(limit=5)
        self.assertEqual(trades[0]["result"], "loss")
        self.assertEqual(execution.consecutive_losses_today(), 1)

    def test_unfilled_entry_cancelled_when_stop_touched(self) -> None:
        record = self.place()
        self.fake.bid, self.fake.ask = 80.9, 80.92
        events = self.service.sync()
        self.assertEqual(events[0]["kind"], "cancelled")
        self.assertIn(record["exchange_order_id"], self.fake.cancelled)

    def test_unfilled_entry_cancelled_after_ttl(self) -> None:
        record = self.place()
        old = (datetime.now(timezone.utc) - timedelta(minutes=LIMITS.order_ttl_min + 1)).isoformat()
        execution._update(record["id"], placed_at=old)
        events = self.service.sync()
        self.assertEqual(events[0]["kind"], "cancelled")

    def test_kill_switch_cancels_entries_and_blocks(self) -> None:
        record = self.place()
        result = self.service.halt()
        self.assertEqual(result["cancelled"], [record["id"]])
        blocked = self.propose()
        self.assertEqual(blocked["status"], "blocked")
        self.service.resume()
        self.assertEqual(self.propose()["status"], "pending")

    def test_second_entry_blocked_while_one_is_resting(self) -> None:
        self.place()
        self.assertEqual(self.propose()["status"], "blocked")

    def test_daily_loss_from_equity_drop(self) -> None:
        self.service.status()  # snapshots start-of-day equity at 300
        self.fake.total = 287.0
        self.assertEqual(self.propose()["status"], "blocked")

    def test_close_position_only_closes_requested_symbol(self) -> None:
        self.fake.positions_list = [
            {"symbol": "HYPEUSDT", "side": "long", "qty": 6.0},
            {"symbol": "BTCUSDT", "side": "short", "qty": 0.01},
        ]
        self.service.close_position("HYPEUSDT")
        self.assertEqual(self.fake.closes, [("HYPEUSDT", "long", 6.0)])


class SuggestTests(unittest.TestCase):
    def test_context_side_follows_levels_not_bias(self) -> None:
        from agent.context import TradeContext

        ctx = TradeContext(symbol="HYPEUSDT", bias="short", poi_low=69.35, poi_high=69.65,
                           invalidation=69.1, targets=[70.5], wave="hunt long")
        with patch("agent.context.get_context", return_value=ctx):
            out = execution.suggest_from_context("HYPEUSDT")
        self.assertEqual((out["side"], out["entry"], out["stop"], out["take_profit"]), ("long", 69.5, 69.1, 70.5))
        self.assertIn("bias del contexto = short", out["rationale"])

    def test_verdict_with_stale_context_is_refused(self) -> None:
        verdict = {"direction": "long", "levels": {"entry": 1, "stop": 0.9, "target": 1.3, "context_stale": True}}
        with self.assertRaises(ExecutionError):
            execution.suggest_from_verdict(verdict, "HYPEUSDT")

    def test_verdict_levels_mapped(self) -> None:
        verdict = {"direction": "short", "grade": "A+", "levels": {"entry": 1.0, "stop": 1.1, "target": 0.8}}
        out = execution.suggest_from_verdict(verdict, "hypeusdt")
        self.assertEqual((out["side"], out["take_profit"], out["symbol"]), ("short", 0.8, "HYPEUSDT"))


if __name__ == "__main__":
    unittest.main()
