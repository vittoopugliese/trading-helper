"""Measure actual retrieval offline against a disposable copy of the local index."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

import chromadb
from sentence_transformers import SentenceTransformer
from agent import knowledge
from agent.context import get_context

model_root = Path.home() / ".cache/huggingface/hub/models--sentence-transformers--paraphrase-multilingual-MiniLM-L12-v2"
revision = (model_root / "refs/main").read_text().strip()
model_path = model_root / "snapshots" / revision

with patch("requests.sessions.Session.request", side_effect=AssertionError("Audit is offline")):
    model = SentenceTransformer(str(model_path), local_files_only=True, device="cpu")
    print("Local embedding model loaded", flush=True)
    # TemporaryDirectory cleanup cannot close Chroma's Rust handles on Windows.
    # Keep the snapshot in the OS temporary directory; it is never used by the app.
    temporary = Path(tempfile.mkdtemp(prefix="trading-helper-audit-"))
    shutil.copytree(ROOT / "data/chroma", temporary / "chroma")
    client = chromadb.PersistentClient(path=str(temporary / "chroma"))
    collection = client.get_collection("trading_notes", embedding_function=None)
    docs = collection.get(include=["documents", "metadatas"])
    lengths = [len(model.tokenizer.encode(d, truncation=False)) for d in docs["documents"]]
    queries = [
        ("actual_hype_context_core", {"structure": {"trend": "bullish"},
            "order_flow": {"bias": "bullish", "buy_ratio": 0.7},
            "indicators": {"volume_spike": True, "rsi": 50}},
            get_context("HYPEUSDT").to_dict(), "core"),
        ("long_wave2_core", {"structure": {"trend": "bullish"},
            "order_flow": {"bias": "bullish", "buy_ratio": 0.7},
            "indicators": {"volume_spike": True, "rsi": 35}},
            {"wave": "fin onda 2", "bias": "long", "invalidation": 99.0}, "core"),
        ("short_wave5_core", {"structure": {"trend": "bearish"},
            "order_flow": {"bias": "bearish", "buy_ratio": 0.3},
            "indicators": {"volume_spike": False, "rsi": 65}},
            {"wave": "fin onda 5", "bias": "short", "invalidation": 101.0}, "core"),
        ("neutral_core", {"structure": {"trend": "neutral"},
            "order_flow": {"bias": "mixed"}, "indicators": {"volume_spike": False, "rsi": 50}},
            None, "core"),
        ("long_wave2_all", {"structure": {"trend": "bullish"},
            "order_flow": {"bias": "bullish", "buy_ratio": 0.7},
            "indicators": {"volume_spike": True, "rsi": 35}},
            {"wave": "fin onda 2", "bias": "long", "invalidation": 99.0}, "all"),
    ]
    output = {"model_revision": revision, "max_seq_length": model.max_seq_length,
        "indexed_chunks": len(lengths), "chunks_exceeding_embedding_limit": sum(n > model.max_seq_length for n in lengths),
        "queries": []}
    for name, snapshot, ctx, scope in queries:
        query = knowledge.build_search_query(snapshot, ctx)
        tokens = model.tokenizer.encode(query, truncation=False)
        truncated = model.tokenize([query])["input_ids"][0].tolist()
        vector = model.encode([query]).tolist()
        results = collection.query(query_embeddings=vector, n_results=8,
            where=knowledge._scope_where(None, scope), include=["documents", "metadatas", "distances"])
        output["queries"].append({"name": name, "query": query,
            "query_tokens": len(tokens), "tokens_passed_to_model": len(truncated),
            "actual_decoded_query": model.tokenizer.decode(truncated),
            "hits": [{"id": ident, "source_file": meta["source_file"],
                      "similarity": round(1-dist, 4), "content": doc}
                     for ident, meta, dist, doc in zip(results["ids"][0], results["metadatas"][0],
                        results["distances"][0], results["documents"][0])]})
    # Isolate the effect of placing Elliott context after the fixed query prefix.
    base = queries[1][1]
    one = knowledge.build_search_query(base, {"wave": "fin onda 2", "bias": "long", "invalidation": 99})
    two = knowledge.build_search_query(base, {"wave": "fin onda 5", "bias": "short", "invalidation": 101})
    output["different_elliott_context_same_model_input"] = (
        model.tokenize([one])["input_ids"].tolist() == model.tokenize([two])["input_ids"].tolist())
    Path(__file__).with_name("retrieval-evidence.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**output, "queries": [{**q, "hits": [{k:v for k,v in h.items() if k != "content"} for h in q["hits"]]} for q in output["queries"]]}, ensure_ascii=True, indent=2))
