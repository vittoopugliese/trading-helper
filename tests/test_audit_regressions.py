"""Regression tests for the 2026-10-12 audit (reports/audits/2026-10-12/auditoria.md).

Each test reproduces a case the audit's probe.py found and asserts the corrected behaviour.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

from agent import knowledge, metrics, policy, scalp_alerts, setup_engine, signal_ledger, watcher
from agent.context import ContextValidationError, TradeContext


def _snapshot(**overrides) -> metrics.MarketSnapshot:
    base = dict(
        symbol="AUDITUSDT", timeframe="1m", trade_type="scalp", price=100.0, timestamp="synthetic",
        indicators={"volume_ratio": 2.0, "volume_spike": True},
        order_flow={"source": "aggTrades", "bias": "bullish", "buy_ratio": 0.7, "book_bias": "bullish",
                    "cvd_divergence": None},
        structure={"trend": "bullish", "recent_swings": [{"kind": "low"}, {"kind": "high"}]},
        derivatives={"oi_change_1h": 1.0, "funding_extreme": False},
    )
    base.update(overrides)
    return metrics.MarketSnapshot(**base)


def _ctx(**overrides) -> TradeContext:
    base = dict(symbol="AUDITUSDT", bias="long", trade_type="scalp", poi_low=99.9, poi_high=100.1,
                invalidation=99.0, targets=[103.0])
    base.update(overrides)
    return TradeContext(**base)


def _candles(n: int = 40, price: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({"open_time": pd.date_range("2026-10-12", periods=n, freq="min", tz="UTC"),
                         "open": [price] * n, "high": [price + 0.2] * n, "low": [price - 0.2] * n,
                         "close": [price] * n, "volume": [1000.0] * n, "taker_buy_base": [600.0] * n})


class _Isolated(unittest.TestCase):
    """No network, no real journal: the decision must depend only on the inputs."""

    def setUp(self) -> None:
        self._patches = [
            patch.object(metrics.market_data, "load_klines_df", return_value=_candles()),
            patch.object(policy, "_journal_rows", return_value=[]),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()

    def verdict(self, snap, ctx, hunt=("long",), buy_ratio=0.7) -> dict:
        v = setup_engine.evaluate_setup(snap, ctx).to_dict()
        v["_buy_ratio"] = buy_ratio
        return watcher.enrich_verdict_dict(v, list(hunt))


class DecisionGateTests(_Isolated):
    def test_no_trade_never_mechanical_entry(self) -> None:
        # Audit P0-1: R:R = 1 with score 94.5% produced NO_TRADE + mechanical_entry=true.
        v = self.verdict(_snapshot(), _ctx(targets=[101.0]))
        self.assertEqual(v["grade"], "NO_TRADE")
        self.assertFalse(v["eligible"])
        self.assertFalse(v["mechanical_entry"])
        self.assertTrue(any("R:R" in r for r in v["blocked_reasons"]))

    def test_clean_setup_is_eligible(self) -> None:
        v = self.verdict(_snapshot(), _ctx())
        self.assertEqual(v["grade"], "A+")
        self.assertTrue(v["eligible"], v["blocked_reasons"])
        self.assertTrue(v["mechanical_entry"])

    def test_atas_against_blocks_entry(self) -> None:
        snap = _snapshot(atas={"available": True, "tf": "1m", "recent_signals": {"long": [], "short": ["delta_flip"]}})
        v = self.verdict(snap, _ctx())
        self.assertEqual(v["grade"], "B")
        self.assertFalse(v["mechanical_entry"])
        self.assertIn("footprint ATAS en contra", v["blocked_reasons"])

    def test_higher_timeframes_against_block_a_plus(self) -> None:
        # Audit P1-6: three bearish HTFs still produced A+ long 0.985.
        snap = _snapshot(context_timeframes={
            label: {"structure": {"trend": "bearish"}} for label in ("intraday", "swing", "macro")
        })
        v = self.verdict(snap, _ctx())
        self.assertNotEqual(v["grade"], "A+")
        self.assertFalse(v["eligible"])
        self.assertTrue(any("contra tendencia HTF" in r for r in v["blocked_reasons"]))

    def test_daily_stop_blocks(self) -> None:
        today = datetime.now().astimezone().isoformat()
        losses = [{"result": "loss", "closed_at": today, "created_at": today}] * 2
        with patch.object(policy, "_journal_rows", return_value=losses):
            v = self.verdict(_snapshot(), _ctx())
        self.assertFalse(v["eligible"])
        self.assertTrue(any("perdidas seguidas" in r for r in v["blocked_reasons"]))


class ContextTests(_Isolated):
    HYPE_LIKE = dict(symbol="HYPEUSDT", bias="short", poi_low=69.35, poi_high=69.65, invalidation=69.1,
                     targets=[70.5, 70.8, 71.2],
                     fib={"short_poi_low": 70.65, "short_poi_high": 70.95, "short_invalidation": 71.1,
                          "short_target_1": 69.8, "short_target_2": 69.35})

    def test_bias_vs_levels_contradiction_detected(self) -> None:
        # Audit P0-4: bias short with long levels gave a "short" entry 69.5 / SL 69.1 / TP 70.5.
        ctx = TradeContext(**self.HYPE_LIKE)
        self.assertTrue(any("describen un long" in i for i in ctx.validate()))
        self.assertEqual(set(ctx.scenarios()), {"long", "short"})
        levels = metrics.suggest_levels(pd.DataFrame({"close": [69.5]}), "short", ctx)
        self.assertGreater(levels["stop"], levels["entry"])
        self.assertLess(levels["target"], levels["entry"])

    def test_engine_blocks_incoherent_context(self) -> None:
        ctx = TradeContext(**self.HYPE_LIKE)
        v = self.verdict(_snapshot(symbol="HYPEUSDT", price=69.5), ctx, hunt=("long", "short"))
        self.assertFalse(v["eligible"])
        self.assertTrue(any(r.startswith("contexto:") for r in v["blocked_reasons"]))

    def test_expired_context_blocks(self) -> None:
        old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        v = self.verdict(_snapshot(), _ctx(as_of=old))
        self.assertFalse(v["eligible"])
        self.assertTrue(any("vencido" in r for r in v["blocked_reasons"]))

    def test_set_context_rejects_and_cache_follows_file(self) -> None:
        from agent import context as context_mod

        with tempfile.TemporaryDirectory() as tmp, patch.object(context_mod, "_context_dir", return_value=Path(tmp)):
            context_mod.clear_context_cache()
            with self.assertRaises(ContextValidationError):
                context_mod.set_context("ZZZUSDT", {"bias": "short", "poi": {"low": 10, "high": 11},
                                                    "invalidation": 9, "targets": [12]})
            context_mod.set_context("ZZZUSDT", {"bias": "long", "poi": {"low": 10, "high": 11},
                                                "invalidation": 9, "targets": [12]})
            self.assertEqual(context_mod.get_context("ZZZUSDT").bias, "long")
            path = Path(tmp) / "ZZZUSDT.yaml"
            text = path.read_text(encoding="utf-8").replace("bias: long", "bias: none")
            path.write_text(text, encoding="utf-8")
            stat = path.stat()
            import os
            os.utime(path, (stat.st_atime, stat.st_mtime + 5))
            self.assertEqual(context_mod.get_context("ZZZUSDT").bias, "none")  # edit from another process
            context_mod.clear_context_cache()

    def test_no_default_levels_without_context(self) -> None:
        self.assertEqual(watcher.scalp_levels_from_context(None), {})
        self.assertIsNone(watcher.build_scalp_ctx("BTCUSDT", "long", None))

    def test_scalp_ctx_keeps_account_and_risk(self) -> None:
        base = _ctx(risk_pct=7.0, account_size=300.0)
        scalp = watcher.build_scalp_ctx("AUDITUSDT", "long", base)
        self.assertEqual((scalp.risk_pct, scalp.account_size), (7.0, 300.0))


class SizingTests(unittest.TestCase):
    def test_margin_never_exceeds_account(self) -> None:
        # Audit P0-3: $300, 7%, SL 0.5% needed $420 margin at x10.
        with patch.object(policy, "_journal_rows", return_value=[]):
            size = metrics.compute_position_size(100.0, 99.5, account_size=300, risk_pct=7.0)
        self.assertLessEqual(size["margin"], 300)
        self.assertLessEqual(size["risk_amount"], 21)
        self.assertTrue(size["capped_by"])

    def test_usd_cap_applies_to_bigger_account(self) -> None:
        size = metrics.compute_position_size(100.0, 99.0, account_size=1000, risk_pct=7.0)
        self.assertLessEqual(size["risk_amount"], 21.0)
        self.assertIn("max_risk_usd 21.0", size["capped_by"])

    def test_open_trades_consume_exposure(self) -> None:
        size = metrics.compute_position_size(100.0, 99.0, account_size=300, risk_pct=7.0, open_trades=1)
        self.assertLessEqual(size["risk_amount"], 9.0)  # 10% of 300 minus one assumed $21 trade
        blocked = metrics.compute_position_size(100.0, 99.0, account_size=300, risk_pct=7.0, open_trades=2)
        self.assertIn("blocked_reason", blocked)


class PatternTests(unittest.TestCase):
    def test_volume_alone_is_not_a_sweep(self) -> None:
        out = metrics.detect_of_pattern_at_poi({"bias": "mixed", "buy_ratio": 0.5, "volume_spike_at_poi": True}, "long", True)
        self.assertIn("sweep_candidate", out["patterns"])
        self.assertNotIn("liquidity_sweep", out["patterns"])
        self.assertFalse(out["confirmed"])

    def test_flat_market_does_not_fire_absorption(self) -> None:
        # Audit P0-2: flat price + positive delta fired absorption_at_support "now" with R:R 0.1.
        candles = pd.DataFrame({"open": [100.0] * 30, "high": [100.05] * 30, "low": [99.95] * 30,
                                "close": [100.0] * 30, "volume": [1000.0] * 30, "taker_buy_base": [700.0] * 30})
        with patch.object(metrics.market_data, "load_klines_df", return_value=candles), \
             patch.object(scalp_alerts, "_ask_bid_wall_ratio", return_value=(1.0, 100.0, 1.0)), \
             patch.object(scalp_alerts, "recent_oi_rise", return_value=(False, 0.0, 0.0)):
            alerts = scalp_alerts.detect_scalp_alerts(
                "AUDITUSDT", short_resistance=None, long_support=(99.9, 100.1), long_invalidation=99.0,
                long_targets=(100.1,), order_flow={"buy_ratio": 0.7, "book_bias": "bullish"})
        self.assertFalse([a for a in alerts if a.trigger == "absorption_at_support"])

    def test_alert_validation_rejects_low_rr(self) -> None:
        alert = scalp_alerts.ScalpAlert("long", "x", "now", 100.0, 100.0, 99.0, 100.1, "")
        self.assertTrue(any("R:R" in r for r in scalp_alerts.validate_alert(alert)))


class LedgerTests(unittest.TestCase):
    def test_cooldown_is_per_kind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch.object(signal_ledger, "db_path", return_value=Path(tmp) / "s.db"):
            now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
            kw = dict(symbol="HYPEUSDT", tf="5m", direction="long", price=10.0, stop=9.5, target=11.0, now=now)
            self.assertIsNotNone(signal_ledger.record(kind="setup_watch", **kw))
            self.assertIsNotNone(signal_ledger.record(kind="a_plus", **kw))
            self.assertIsNone(signal_ledger.record(kind="a_plus", **kw))


class KnowledgeTests(unittest.TestCase):
    def test_unknown_scope_raises(self) -> None:
        with self.assertRaises(ValueError):
            knowledge._scope_where(None, "cor")

    def test_chunks_fit_embedding_and_keep_heading(self) -> None:
        text = "# Nota\n\n====================\nREGLAS\n====================\n" + "\n".join(
            f"- regla {i}: el precio toca el POI y el delta cambia de signo con volumen sobre la media" for i in range(40))
        chunks = knowledge.chunk_note(text, "Nota")
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(knowledge.count_tokens(c["text"]), knowledge.EMBED_MAX_TOKENS)
            self.assertTrue(c["text"].startswith("[Nota > REGLAS]"))
            self.assertNotIn("====", c["text"])

    def test_doc_types(self) -> None:
        self.assertEqual(knowledge.doc_type_for("notes/conversacion-previa-2026-06-22.md"), "historial")
        self.assertEqual(knowledge.doc_type_for("notes/setup-rules/a-plus-checklist.md"), "regla")
        self.assertEqual(knowledge.doc_type_for("notes/journal/x.md"), "ejemplo")

    def test_cold_index_recounts_and_orphans_are_removed(self) -> None:
        collection = MagicMock()
        collection.count.side_effect = [0, 50]
        collection.query.return_value = {"ids": [[]]}
        with patch.object(knowledge, "_get_collection", return_value=collection), \
             patch.object(knowledge, "index_notes") as index:
            knowledge.search_notes("q", top_k=8)
        index.assert_called_once()
        self.assertEqual(collection.query.call_args.kwargs["n_results"], 32)

        collection = MagicMock()
        collection.get.return_value = {"ids": ["gone.md::0"], "metadatas": [{"source_file": "notes/gone.md"}]}
        with patch.object(knowledge, "_get_collection", return_value=collection), \
             patch.object(knowledge, "_discover_note_files", return_value=[]):
            stats = knowledge.index_notes(force=True)
        self.assertEqual(stats["removed_orphan_chunks"], 1)

    def test_rules_reach_the_prompt_complete(self) -> None:
        from agent import prompt_context

        text = prompt_context.rules_text()
        self.assertIn("Paso 3", text)  # used to be cut at 2000 chars, before the 4 signals
        self.assertIn("POLITICA ACTIVA", prompt_context.policy_block())


if __name__ == "__main__":
    unittest.main()
