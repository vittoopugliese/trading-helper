from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from agent import atas_feed, liquidations, signal_ledger


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")


class _TempDb(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self._patches = [
            patch.object(atas_feed, "db_path", return_value=tmp / "atas.db"),
            patch.object(liquidations, "db_path", return_value=tmp / "liq.db"),
            patch.object(signal_ledger, "db_path", return_value=tmp / "signals.db"),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self) -> None:
        for p in self._patches:
            p.stop()
        self._tmp.cleanup()


class NormalizeTests(unittest.TestCase):
    def test_symbols(self) -> None:
        self.assertEqual(atas_feed.normalize_symbol("HYPEUSDT"), "HYPEUSDT")
        self.assertEqual(atas_feed.normalize_symbol("hypeusdt.p"), "HYPEUSDT")
        self.assertEqual(atas_feed.normalize_symbol("HYPE-USDT-SWAP"), "HYPEUSDT")
        self.assertIsNone(atas_feed.normalize_symbol(""))

    def test_timeframes(self) -> None:
        self.assertEqual(atas_feed.normalize_timeframe("M15"), "15m")
        self.assertEqual(atas_feed.normalize_timeframe("H1"), "1h")
        self.assertEqual(atas_feed.normalize_timeframe("5m"), "5m")
        self.assertEqual(atas_feed.normalize_timeframe("Daily"), "1d")
        self.assertEqual(atas_feed.normalize_timeframe("500", "Tick"), "tick:500")


class FootprintTests(unittest.TestCase):
    def test_stacked_buy_imbalance(self) -> None:
        # levels: (price, bid, ask). ask[i] vs bid[i-1] >= 3x for three levels in a row.
        levels = [(10.0, 10, 5), (10.1, 10, 40), (10.2, 10, 40), (10.3, 10, 40), (10.4, 50, 5)]
        zones = atas_feed.stacked_imbalances(levels, ratio=3.0, min_stack=3)
        self.assertEqual(zones, [{"side": "buy", "low": 10.1, "high": 10.3, "levels": 3}])

    def test_no_stack_when_two_levels(self) -> None:
        levels = [(10.0, 10, 5), (10.1, 10, 40), (10.2, 10, 40), (10.3, 50, 10)]
        self.assertEqual([z for z in atas_feed.stacked_imbalances(levels, 3.0, 3) if z["side"] == "buy"], [])

    def test_absorption_at_low_and_delta_reversal(self) -> None:
        # Sellers hammer the low (bid >> ask there), price closes in the upper half, delta ends positive.
        bar = {
            "open": 10.2, "high": 10.4, "low": 10.0, "close": 10.35, "volume": 1000,
            "delta": 60, "min_delta": -300, "max_delta": 80,
            "levels": [[10.0, 300, 50], [10.1, 100, 80], [10.2, 50, 120], [10.3, 40, 160], [10.4, 20, 80]],
        }
        f = atas_feed.bar_features(bar)
        self.assertTrue(f["absorption_low"])
        self.assertFalse(f["absorption_high"])
        self.assertTrue(f["delta_reversal_up"])
        self.assertIn("absorcion_en_minimo", atas_feed.long_short_signals(f)["long"])

    def test_cvd_divergence_variants(self) -> None:
        def bars(lows, deltas):
            return [{"low": lo, "high": 11.0, "delta": d} for lo, d in zip(lows, deltas)]

        exhaustion = bars([10] * 5 + [9.5] * 5, [-50] * 5 + [-10] * 5)
        self.assertEqual(atas_feed.cvd_divergence(exhaustion), ("bullish", "agotamiento"))
        absorption = bars([10] * 5 + [10.3] * 5, [-10] * 5 + [-50] * 5)
        self.assertEqual(atas_feed.cvd_divergence(absorption), ("bullish", "absorcion"))
        self.assertEqual(atas_feed.cvd_divergence(exhaustion[:9]), (None, None))

    def test_delta_flip_between_bars(self) -> None:
        prev = {"high": 10, "low": 9, "close": 9.2, "volume": 1000, "delta": -400}
        bar = {"high": 10, "low": 9.1, "close": 9.9, "volume": 800, "delta": 300}
        self.assertTrue(atas_feed.bar_features(bar, prev)["delta_flip_up"])


class IngestTests(_TempDb):
    def _payload(self, bars, **extra):
        return {"source": "atas", "instrument": "HYPEUSDT", "exchange": "Binance", "timeframe": "M5",
                "chart_type": "TimeFrame", "tick_size": 0.001, "bars": bars, **extra}

    def test_closed_bar_is_not_reopened_by_late_live_update(self) -> None:
        t = _iso(datetime(2026, 10, 12, 12, 0))
        atas_feed.ingest(self._payload([{"time": t, "closed": True, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10, "delta": 3}]))
        atas_feed.ingest(self._payload([{"time": t, "closed": False, "open": 1, "high": 2, "low": 0.5, "close": 1.1, "volume": 4, "delta": -1}]))
        bars = atas_feed.load_bars("HYPEUSDT", "5m")
        self.assertEqual((bars[0]["closed"], bars[0]["close"], bars[0]["volume"]), (1, 1.5, 10))

    def test_rejects_foreign_payload(self) -> None:
        with self.assertRaises(atas_feed.IngestError):
            atas_feed.ingest({"source": "other"})

    def test_snapshot_fresh_and_confirmation(self) -> None:
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        bars = []
        for i in range(12):
            bars.append({"time": _iso(now - timedelta(minutes=5 * (12 - i))), "closed": True,
                         "open": 10, "high": 10.4, "low": 10.0, "close": 10.2, "volume": 500, "delta": 10})
        bars.append({"time": _iso(now - timedelta(minutes=5)), "closed": True, "open": 10.2, "high": 10.4, "low": 10.0,
                     "close": 10.35, "volume": 1000, "delta": 60, "min_delta": -300, "max_delta": 80,
                     "levels": [[10.0, 300, 50], [10.1, 100, 80], [10.2, 50, 120], [10.3, 40, 160], [10.4, 20, 80]]})
        atas_feed.ingest(self._payload(bars, trades=[{"time": _iso(now), "first_price": 10.1, "last_price": 10.12, "volume": 900, "direction": "Buy"}]))
        snap = atas_feed.snapshot("HYPEUSDT", "5m")
        self.assertTrue(snap["available"])
        self.assertEqual(snap["big_trades_15m"]["buy_volume"], 900)
        conf = atas_feed.footprint_confirmation(snap, "long")
        self.assertTrue(conf["confirmed"])
        self.assertFalse(atas_feed.footprint_confirmation(snap, "short")["confirmed"])

    def test_stale_feed_is_unavailable(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        atas_feed.ingest(self._payload([{"time": _iso(old), "closed": True, "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 10}]))
        # updated_at is "now" at ingest; age it to simulate ATAS closed long ago.
        with atas_feed._connect() as conn:
            conn.execute("UPDATE bars SET updated_at=?", (atas_feed._iso(old),))
        self.assertFalse(atas_feed.snapshot("HYPEUSDT", "5m")["available"])


class LiquidationTests(_TempDb):
    def test_parsers_side_is_liquidated_position(self) -> None:
        bybit = {"topic": "allLiquidation.HYPEUSDT", "data": [{"T": 1, "s": "HYPEUSDT", "S": "Buy", "v": "10", "p": "40"}]}
        self.assertEqual(liquidations.parse_bybit(bybit)[0][3], "long")
        binance = {"stream": "x", "data": {"e": "forceOrder", "o": {"s": "HYPEUSDT", "S": "BUY", "q": "2", "z": "2", "p": "41", "ap": "41", "T": 5}}}
        row = liquidations.parse_binance(binance)[0]
        self.assertEqual((row[3], row[6]), ("short", 82.0))

    def test_spike_needs_history_then_detects(self) -> None:
        liquidations.init_db()
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        now_ms = int(now.timestamp() * 1000)
        rows = []
        for h in range(1, 24):  # small steady long liquidations every hour
            rows.append(("bybit", "HYPEUSDT", now_ms - h * 3_600_000, "long", 10, 40, 400.0))
        rows.append(("bybit", "HYPEUSDT", now_ms - 60_000, "long", 2500, 40, 100_000.0))  # flush now
        liquidations.store(rows)
        out = liquidations.summary("HYPEUSDT", now=now)
        self.assertTrue(out["ready"])
        self.assertTrue(out["long_spike"])
        self.assertFalse(out["short_spike"])
        self.assertTrue(liquidations.flush_confirmation(out, "long")["flush"])

    def test_not_ready_without_history(self) -> None:
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        liquidations.init_db()
        liquidations.store([("bybit", "HYPEUSDT", int(now.timestamp() * 1000) - 60_000, "long", 1, 40, 40.0)])
        out = liquidations.summary("HYPEUSDT", now=now)
        self.assertFalse(out["ready"])
        self.assertFalse(liquidations.flush_confirmation(out, "long")["available"])


class LedgerTests(_TempDb):
    def test_label_stop_first_on_same_candle(self) -> None:
        candles = [{"high": 11.0, "low": 9.0, "close": 10}]
        out = signal_ledger.label("long", 10.0, 9.5, 11.0, candles, 240, 0.0)
        self.assertEqual((out["status"], out["r_result"]), ("loss", -1.0))

    def test_label_win_with_costs_and_pending(self) -> None:
        candles = [{"high": 10.2, "low": 9.8, "close": 10.1}, {"high": 11.1, "low": 10.0, "close": 11.0}]
        out = signal_ledger.label("long", 10.0, 9.5, 11.0, candles, 240, 7.0)
        self.assertEqual(out["status"], "win")
        self.assertAlmostEqual(out["r_result"], 2 - 2 * 10 * 7 / 10_000 / 0.5, places=4)
        self.assertIsNone(signal_ledger.label("long", 10.0, 9.5, 11.0, candles[:1], 240, 7.0))

    def test_record_geometry_and_cooldown(self) -> None:
        now = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        kw = dict(symbol="HYPEUSDT", tf="5m", kind="a_plus", direction="long", price=10.0, target=11.0)
        self.assertIsNone(signal_ledger.record(stop=10.5, now=now, **kw))  # stop above price
        self.assertIsNotNone(signal_ledger.record(stop=9.5, now=now, **kw))
        self.assertIsNone(signal_ledger.record(stop=9.5, now=now + timedelta(minutes=10), **kw))
        self.assertIsNotNone(signal_ledger.record(stop=9.5, now=now + timedelta(minutes=31), **kw))

    def test_resolve_and_stats_by_feature(self) -> None:
        base = datetime(2026, 10, 12, 12, 0, tzinfo=timezone.utc)
        for i in range(4):
            signal_ledger.record(symbol="HYPEUSDT", tf="5m", kind="a_plus", direction="long", price=10.0,
                                 stop=9.5, target=11.0, features={"atas_confirmed": i % 2 == 0},
                                 now=base + timedelta(hours=i))

        def fake_fetch(symbol, created, limit):
            win = created.hour % 2 == 0
            return [{"high": 11.2 if win else 10.1, "low": 9.9 if win else 9.4, "close": 10}]

        self.assertEqual(signal_ledger.resolve_open(fetch=fake_fetch), 4)
        st = signal_ledger.stats()
        self.assertEqual(st["overall"]["n"], 4)
        self.assertEqual(st["by_feature"]["atas_confirmed"]["con"]["win_rate"], 1.0)
        self.assertEqual(st["by_feature"]["atas_confirmed"]["sin"]["win_rate"], 0.0)
        self.assertFalse(st["by_feature"]["atas_confirmed"]["suficiente"])

    def test_wilson(self) -> None:
        lo, hi = signal_ledger.wilson(40, 100)
        self.assertAlmostEqual(lo, 0.3094, places=3)
        self.assertAlmostEqual(hi, 0.4980, places=3)


class SetupGateTests(unittest.TestCase):
    """With live ATAS data, an A+ at the POI needs the footprint on its side."""

    def _verdict(self, atas_snapshot):
        from agent import metrics, setup_engine
        from agent.context import TradeContext

        ctx = TradeContext(symbol="HYPEUSDT", bias="long", poi_low=9.9, poi_high=10.1, invalidation=9.5,
                           targets=[11.5], wave="2", trade_type="scalp")
        snap = metrics.MarketSnapshot(
            symbol="HYPEUSDT", timeframe="5m", trade_type="scalp", price=10.0, timestamp="t",
            indicators={"volume_spike": True, "volume_ratio": 2.0},
            order_flow={"bias": "bullish", "buy_ratio": 0.6, "book_bias": "bullish"},
            structure={"trend": "bullish", "recent_swings": [1, 2]},
            derivatives={"oi_change_1h": 5.0, "funding_rate": 0.0001, "funding_extreme": False},
            atas=atas_snapshot, liquidations={"ready": False},
        )
        levels = {"entry": 10.0, "stop": 9.5, "target": 11.5, "risk_reward": 3.0, "source": "context"}
        with patch("agent.market_data.load_klines_df", return_value=None),              patch("agent.metrics.suggest_levels", return_value=levels),              patch("agent.metrics.compute_position_size", return_value={}):
            return setup_engine.evaluate_setup(snap, trade_context=ctx)

    def _atas(self, long_signals, short_signals):
        return {"available": True, "tf": "5m", "recent_signals": {"long": long_signals, "short": short_signals}}

    def test_newest_signal_wins(self) -> None:
        from agent import atas_feed

        atas = {"available": True, "tf": "5m",
                "recent_signals": {"long": ["delta_flip", "imbalance_apilado_comprador"], "short": ["delta_flip"]},
                "latest_signals": {"long": ["delta_flip", "imbalance_apilado_comprador"], "short": [], "ts": "2026-10-13T02:00:00"}}
        self.assertFalse(atas_feed.footprint_confirmation(atas, "short")["confirmed"])
        self.assertTrue(atas_feed.footprint_confirmation(atas, "short")["contradicted"])
        self.assertTrue(atas_feed.footprint_confirmation(atas, "long")["confirmed"])

    def test_a_plus_without_atas_stands(self) -> None:
        v = self._verdict({"available": False, "reason": "sin datos"})
        self.assertEqual(v.grade, "A+")
        self.assertFalse(v.evidence["atas"]["available"])

    def test_a_plus_needs_footprint_trigger_when_atas_live(self) -> None:
        self.assertEqual(self._verdict(self._atas(["absorcion_en_minimo"], [])).grade, "A+")
        self.assertEqual(self._verdict(self._atas([], [])).grade, "B")
        self.assertEqual(self._verdict(self._atas([], ["imbalance_apilado_vendedor"])).grade, "B")


if __name__ == "__main__":
    unittest.main()
