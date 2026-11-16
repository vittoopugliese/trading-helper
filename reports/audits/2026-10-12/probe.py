"""Offline audit probes. Production databases are opened read-only; no API calls.

Run from the repository root: .venv/Scripts/python.exe reports/audits/2026-10-12/probe.py
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import pandas as pd
from agent import knowledge, metrics, scalp_alerts, setup_engine, watcher
from agent.config import get_config
from agent.context import TradeContext, get_context


def corpus_audit():
    cfg = get_config()["notes"]
    files = knowledge._discover_note_files()
    discovered = {}
    for path in files:
        content = path.read_text(encoding="utf-8")
        chunks = knowledge._chunk_by_sections(content, cfg["chunk_size"])
        discovered[path.relative_to(ROOT).as_posix()] = {
            "chars": len(content), "chunks": len(chunks),
            "tiny_chunks_lt_80_chars": sum(len(c) < 80 for c in chunks),
            "file_hash": hashlib.md5(path.read_bytes()).hexdigest(),
        }
    db = ROOT / "data/chroma/chroma.sqlite3"
    with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as conn:
        rows = conn.execute("""
            SELECT e.embedding_id, m.key, m.string_value, m.int_value
            FROM embeddings e JOIN embedding_metadata m ON e.id = m.id
            JOIN segments s ON e.segment_id = s.id
            JOIN collections c ON s.collection = c.id WHERE c.name = 'trading_notes'
        """).fetchall()
    records = defaultdict(dict)
    for ident, key, string_value, int_value in rows:
        records[ident][key] = string_value if string_value is not None else int_value
    indexed_files = Counter(r.get("source_file", "unknown") for r in records.values())
    by_category = Counter(r.get("category", "unknown") for r in records.values())
    mismatches = sorted({r["source_file"] for r in records.values()
        if r.get("source_file") in discovered
        and r.get("file_hash") != discovered[r["source_file"]]["file_hash"]})
    indexed_set = set(indexed_files)
    all_files = {p.relative_to(ROOT).as_posix() for p in (ROOT / "notes").rglob("*") if p.is_file()}
    docs = [str(r.get("chroma:document", "")) for r in records.values()]
    return {
        "discovered_file_count": len(files),
        "current_chunk_count_if_reindexed": sum(d["chunks"] for d in discovered.values()),
        "indexed_chunk_count": len(records), "indexed_file_count": len(indexed_files),
        "indexed_categories": dict(by_category), "indexed_files": dict(indexed_files.most_common()),
        "not_indexed": sorted(set(discovered) - indexed_set),
        "orphaned_index_files": sorted(indexed_set - set(discovered)),
        "hash_mismatches": mismatches, "excluded_files": sorted(all_files - set(discovered)),
        "indexed_chunks_lt_80_chars": sum(len(d) < 80 for d in docs),
        "indexed_chunks_gt_300_chars": sum(len(d) > 300 for d in docs),
        "indexed_chunks_gt_400_chars": sum(len(d) > 400 for d in docs),
        "duplicate_document_occurrences": len(docs) - len(set(docs)),
        "source_inventory": discovered,
    }


def decision_probes():
    frame = pd.DataFrame({"close": [100.0]})
    ctx = TradeContext(symbol="AUDITUSDT", bias="long", trade_type="scalp",
        poi_low=99.9, poi_high=100.1, invalidation=99.0, targets=[101.0])
    snap = metrics.MarketSnapshot(symbol="AUDITUSDT", timeframe="1m", trade_type="scalp",
        price=100.0, timestamp="synthetic",
        indicators={"volume_ratio": 2.0, "volume_spike": True},
        order_flow={"source": "aggTrades", "bias": "bullish", "buy_ratio": 0.7,
                    "book_bias": "bullish", "cvd_divergence": None},
        structure={"trend": "bullish", "recent_swings": [{"kind": "low"}, {"kind": "high"}]},
        derivatives={"oi_change_1h": 1.0, "funding_extreme": False})
    with patch.object(metrics.market_data, "load_klines_df", return_value=frame):
        verdict = setup_engine.evaluate_setup(snap, ctx).to_dict()
    verdict["_buy_ratio"] = 0.7
    verdict = watcher.enrich_verdict_dict(verdict, ["long"])
    assert verdict["grade"] == "NO_TRADE" and verdict["mechanical_entry"]
    ctx.targets = [103.0]
    snap.context_timeframes = {tf: {"structure": {"trend": "bearish"},
        "order_flow": {"bias": "bearish"}} for tf in ("intraday", "swing", "macro")}
    with patch.object(metrics.market_data, "load_klines_df", return_value=frame):
        opposite_htf = setup_engine.evaluate_setup(snap, ctx).to_dict()
    assert opposite_htf["grade"] == "A+"
    snap.atas = {"available": True, "tf": "1m", "recent_signals": {"long": [], "short": ["delta_flip"]}}
    with patch.object(metrics.market_data, "load_klines_df", return_value=frame):
        atas_blocked = setup_engine.evaluate_setup(snap, ctx).to_dict()
    atas_blocked["_buy_ratio"] = 0.7
    atas_blocked = watcher.enrich_verdict_dict(atas_blocked, ["long"])
    assert atas_blocked["grade"] == "B" and atas_blocked["mechanical_entry"]

    live_ctx = get_context("HYPEUSDT")
    assert live_ctx is not None
    actual_levels = metrics.suggest_levels(pd.DataFrame({"close": [live_ctx.poi_mid()]}), live_ctx.bias, live_ctx)
    sane = scalp_alerts.levels_sane(live_ctx.bias, actual_levels["entry"], actual_levels["stop"], actual_levels["target"])

    size = metrics.compute_position_size(100.0, 99.5, account_size=300, risk_pct=7.0)
    size["derived_notional"] = size["position_size"] * 100
    size["derived_margin_at_10x"] = size["derived_notional"] / 10
    large_size = metrics.compute_position_size(100.0, 99.0, account_size=1000, risk_pct=7.0)

    artificial_flow = {"bias": "mixed", "buy_ratio": 0.5, "volume_spike_at_poi": True}
    patterns = metrics.detect_of_pattern_at_poi(artificial_flow, "long", True)
    assert "liquidity_sweep" in patterns["patterns"] and patterns["confirmed"]

    # Positive delta alone triggers the legacy absorption branch; R:R is 0.1.
    candles = pd.DataFrame({"open": [100.0]*30, "high": [100.05]*30,
        "low": [99.95]*30, "close": [100.0]*30,
        "volume": [1000.0]*30, "taker_buy_base": [700.0]*30})
    with patch.object(metrics.market_data, "load_klines_df", return_value=candles), \
         patch.object(metrics, "compute_real_order_flow", return_value={"buy_ratio": 0.7, "book_bias": "bullish"}), \
         patch.object(scalp_alerts, "_ask_bid_wall_ratio", return_value=(1.0, 100.0, 1.0)), \
         patch.object(scalp_alerts, "recent_oi_rise", return_value=(False, 0.0, 0.0)):
        alerts = scalp_alerts.detect_scalp_alerts("AUDITUSDT", short_resistance=None,
            long_support=(99.9, 100.1), long_invalidation=99.0, long_targets=(100.1,))
    assert any(a.urgency == "now" for a in alerts)
    return {"no_trade_but_mechanical_entry": verdict,
        "opposite_higher_timeframes": {"grade": opposite_htf["grade"], "score": opposite_htf["score"],
            "direction": opposite_htf["direction"], "htf_trends": "bearish in every supplied higher timeframe"},
        "atas_against_but_mechanical_entry": {"grade": atas_blocked["grade"], "mechanical_entry": atas_blocked["mechanical_entry"],
            "atas_evidence": atas_blocked["evidence"]["atas"]},
        "actual_context_at_poi_mid": {"bias": live_ctx.bias, "levels": actual_levels, "directional_levels_sane": sane},
        "margin_exceeds_account": size, "configured_usd_risk_cap_ignored": large_size,
        "volume_spike_without_sweep_price_evidence": patterns,
        "scalp_now_without_min_rr_or_absorption_sequence": [a.to_dict() for a in alerts],
        "scalp_context_risk_pct": watcher.build_scalp_ctx("HYPEUSDT", "long", live_ctx).risk_pct,
        "stored_context_risk_pct": live_ctx.risk_pct}


def indexing_probes():
    collection = MagicMock()
    collection.count.return_value = 0
    collection.query.return_value = {"ids": [[]]}
    with patch.object(knowledge, "_get_collection", return_value=collection), \
         patch.object(knowledge, "index_notes") as index:
        knowledge.search_notes("test query", top_k=8)
        cold = {"index_called": index.called, "requested_top_k": 8,
                "actual_n_results": collection.query.call_args.kwargs["n_results"]}
    with patch.object(knowledge, "_get_collection", return_value=collection), \
         patch.object(knowledge, "_discover_note_files", return_value=[]):
        stats = knowledge.index_notes(force=True)
    return {"cold_index_first_query": cold,
            "force_reindex_with_no_source_files": {"stats": stats, "delete_called": collection.delete.called},
            "scope_core_filter": knowledge._scope_where(None, "core"),
            "heading_split_example": knowledge._chunk_by_sections("# Parent\n\n## Child\nA complete rule.", 500)}


def rule_truncation():
    out = {}
    for p in (ROOT / ".cursor/rules").glob("*.mdc"):
        text = p.read_text(encoding="utf-8")
        out[p.name] = {"total_chars": len(text), "retained_chars": min(2000, len(text)),
            "chars_removed": max(0, len(text)-2000), "last_140_chars_passed_to_chat": text[:2000][-140:]}
    return out


def scan_fetch_counts():
    """Count fetch invocations with synthetic, coherent return values, no network/cache writes.

    Each live load_klines_df refreshes the tail at least once; backfill can add requests.
    """
    calls = Counter()
    frame = pd.DataFrame({"open_time": pd.date_range("2026-10-12", periods=200, freq="min", tz="UTC"),
        "open": [69.5]*200, "high": [69.6]*200, "low": [69.4]*200, "close": [69.5]*200,
        "volume": [1000.0]*200, "taker_buy_base": [700.0]*200})
    trades = [{"qty": 1.0, "price": 69.5, "is_buyer_maker": False, "time": i} for i in range(1000)]
    fixtures = {
        "load_klines_df": frame, "update_cache": 0, "fetch_ticker_price": 69.5,
        "fetch_agg_trades": trades, "fetch_depth": {"bids": [[69.4, 10.0]], "asks": [[69.6, 5.0]]},
        "fetch_open_interest": {"open_interest": 1000},
        "fetch_open_interest_hist": [{"open_interest": 1000}, {"open_interest": 1010}],
        "fetch_funding_rate": [{"funding_rate": 0.0}],
        "fetch_long_short_ratio": [{"long_short_ratio": 1.0, "long_account": 0.5}],
    }
    def fake(name, result):
        def wrapped(*args, **kwargs):
            calls[name] += 1
            return result
        return wrapped
    with ExitStack() as stack:
        for name, result in fixtures.items():
            stack.enter_context(patch.object(metrics.market_data, name, side_effect=fake(name, result)))
        stack.enter_context(patch.object(metrics, "compute_external_flow", return_value=({}, {})))
        stack.enter_context(patch.object(watcher.signal_ledger, "record"))
        stack.enter_context(patch.object(watcher, "notify_desktop"))
        watcher.run_scan("HYPEUSDT", "1m", scalp_mode=True, bias="both")
    return {"invocations": dict(calls), "minimum_rest_requests_without_backfill": sum(calls.values()),
            "mode": "one HYPEUSDT scalp cycle, both biases, primary 1m"}


if __name__ == "__main__":
    # Any unexpected HTTP request is a probe error, not permission to query live services.
    with patch("requests.sessions.Session.request", side_effect=AssertionError("Audit is offline")):
        output = {"corpus": corpus_audit(), "decision_probes": decision_probes(),
                  "indexing_probes": indexing_probes(), "chat_rules": rule_truncation(),
                  "scan_fetch_counts": scan_fetch_counts()}
    output["audited_at_utc"] = datetime.now(timezone.utc).isoformat()
    audited = ["agent/knowledge.py", "agent/metrics.py", "agent/setup_engine.py", "agent/watcher.py",
        "agent/scalp_alerts.py", "agent/analyst.py", "agent/context.py", "agent/market_data.py",
        "agent/atas_feed.py", "agent/liquidations.py", "agent/signal_ledger.py", "web/chat.py",
        "web/services.py", "web/static/cockpit.js", "config.yaml", "context/HYPEUSDT.yaml"]
    output["source_sha256"] = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in audited}
    destination = Path(__file__).with_name("evidence.json")
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    compact = {**output, "corpus": {k:v for k,v in output["corpus"].items() if k != "source_inventory"}}
    print(json.dumps(compact, ensure_ascii=True, indent=2))
