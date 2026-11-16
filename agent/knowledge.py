"""Index and search trading notes with ChromaDB.

Pipeline (version PIPELINE_VERSION, part of every stored hash so a chunker/model change
reindexes everything):
- Notes are split into rule units: a markdown/==== section is cut on line boundaries into
  pieces that fit the embedding model's real window (128 tokens), and every piece carries
  the heading path of its section ("archivo > seccion"), so a retrieved piece still says
  what it belongs to.
- Each file gets a `doc_type` (regla / teoria / libro / ejemplo / investigacion / historial).
  `scope="core"` means the active rules + theory, never old conversations or the journal.
- Search returns at most 2 pieces per file and, by default, expands each hit to its whole
  section so a condition is not served without its exception or invalidation.
- The active risk policy and the gatillo are NOT left to retrieval: prompts inject them in
  full (agent/policy.describe, .cursor/rules). RAG is for explanation and examples.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

from agent.config import PROJECT_ROOT, get_config, resolve_path

COLLECTION_NAME = "trading_notes"
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"
EMBED_MAX_TOKENS = 128          # sentence-transformers max_seq_length of the model
CHUNK_TOKEN_BUDGET = 120        # leave room for special tokens
PIPELINE_VERSION = "2"
MAX_PER_FILE = 2
SECTION_EXPAND_CHARS = 1500

_collection = None
_embedding_fn = None

BOOKS_CATEGORY = "libros"
CORE_DOC_TYPES = ["regla", "teoria", "general"]
HISTORY_DOC_TYPES = ["ejemplo", "investigacion", "historial"]


def doc_type_for(rel_path: str) -> str:
    """Authority of a note, from its location. Rules beat theory beat history."""
    parts = rel_path.split("/")
    name = parts[-1].lower()
    folder = parts[1] if len(parts) > 2 else ""
    if folder == "libros":
        return "libro"
    if folder == "journal":
        return "ejemplo"
    if folder == "research" or name.startswith("backtest"):
        return "investigacion"
    if name.startswith(("conversacion", "cursor_")):
        return "historial"
    if folder in ("setup-rules", "order-flow") and not name.endswith(".txt"):
        return "regla"
    if folder in ("smart-money", "elliott", "order-flow"):
        return "teoria"  # course transcripts (.txt) and concept notes
    return "general"


def _scope_where(category: str | None, scope: str | None) -> dict[str, Any] | None:
    """Translate (category, scope) into a Chroma `where` filter.

    scope: "core" (rules + theory, no books, no history) | "books" | "history"
    (journal, research, old conversations) | "all". An explicit category wins.
    """
    if category:
        return {"category": category}
    scope = (scope or "all").lower()
    if scope == "core":
        return {"doc_type": {"$in": CORE_DOC_TYPES}}
    if scope == "books":
        return {"doc_type": "libro"}
    if scope == "history":
        return {"doc_type": {"$in": HISTORY_DOC_TYPES}}
    if scope == "all":
        return None
    raise ValueError(f"scope desconocido: {scope}")


def _get_embedding_function():
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=EMBEDDING_MODEL)
    return _embedding_fn


@lru_cache(maxsize=1)
def _tokenizer():
    try:
        from transformers import AutoTokenizer

        return AutoTokenizer.from_pretrained(f"sentence-transformers/{EMBEDDING_MODEL}")
    except Exception:
        return None


def count_tokens(text: str) -> int:
    tok = _tokenizer()
    if tok is None:
        return max(1, len(text) // 3)  # conservative estimate for Spanish text
    return len(tok(text, add_special_tokens=True)["input_ids"])


def _get_client() -> chromadb.PersistentClient:
    return chromadb.PersistentClient(path=str(_chroma_path()))


def _get_collection():
    global _collection
    if _collection is None:
        client = _get_client()
        _collection = client.get_or_create_collection(
            name=COLLECTION_NAME,
            embedding_function=_get_embedding_function(),
            metadata={"hnsw:space": "cosine"},
        )
    return _collection


def _notes_folder() -> Path:
    cfg = get_config()
    return resolve_path(cfg["notes"]["folder"])


def _chroma_path() -> Path:
    cfg = get_config()
    path = resolve_path(cfg["paths"]["chroma_dir"])
    path.mkdir(parents=True, exist_ok=True)
    return path


# --------------------------------------------------------------------------- chunking


def _is_rule_line(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 10 and set(stripped) <= {"=", "-"}


def _sections(text: str) -> list[dict[str, Any]]:
    """Split into sections keeping the heading path and the starting line (1-based)."""
    lines = text.splitlines()
    sections: list[dict[str, Any]] = []
    path: list[tuple[int, str]] = []  # (level, title)
    current: list[str] = []
    start = 1
    banner = None  # None | "open" (rule seen, waiting title) | "titled" (title seen, closing rule next)

    def flush(end_line: int) -> None:
        body = [ln for ln in current if not _is_rule_line(ln)]
        if any(ln.strip() for ln in body):
            sections.append({"heading": " > ".join(t for _, t in path), "lines": body, "line_start": start})

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        md = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if md:
            flush(i)
            level = len(md.group(1))
            path[:] = [p for p in path if p[0] < level] + [(level, md.group(2).strip())]
            current, start = [], i + 1
            continue
        if _is_rule_line(line):
            if banner == "titled":
                # Closing rule of a "====\nTITLE\n====" banner.
                banner = None
                start = i + 1
                continue
            # Opening rule: the next non-empty line is the section title.
            flush(i)
            current, start = [], i + 1
            banner = "open"
            continue
        if banner == "open" and stripped:
            path[:] = [p for p in path if p[0] < 2] + [(2, stripped)]
            banner = "titled"
            start = i + 1
            continue
        if banner == "titled" and stripped:
            banner = None  # single rule line above a title: no closing rule
        current.append(line)
    flush(len(lines) + 1)
    return sections


def _split_long_line(line: str, budget: int, prefix_tokens: int) -> list[str]:
    sentences = re.split(r"(?<=[.;:])\s+", line)
    out, buf = [], ""
    for sent in sentences:
        candidate = f"{buf} {sent}".strip()
        if buf and count_tokens(candidate) + prefix_tokens > budget:
            out.append(buf)
            buf = sent
        else:
            buf = candidate
    if buf:
        out.append(buf)
    return out


def chunk_note(text: str, title: str, budget: int = CHUNK_TOKEN_BUDGET) -> list[dict[str, Any]]:
    """Rule-unit chunks that fit the embedding window, each prefixed with its heading path."""
    chunks: list[dict[str, Any]] = []
    for sec_index, sec in enumerate(_sections(text)):
        heading = sec["heading"] or title
        prefix = f"[{heading}]\n"
        prefix_tokens = count_tokens(prefix)
        units: list[tuple[int, str]] = []
        for offset, line in enumerate(sec["lines"]):
            if not line.strip():
                continue
            line_no = sec["line_start"] + offset
            if count_tokens(line) + prefix_tokens > budget:
                units += [(line_no, part) for part in _split_long_line(line.strip(), budget, prefix_tokens)]
            else:
                units.append((line_no, line.rstrip()))
        buf: list[str] = []
        buf_start = None
        for line_no, unit in units:
            candidate = "\n".join(buf + [unit])
            if buf and count_tokens(prefix + candidate) > budget:
                chunks.append({"text": prefix + "\n".join(buf), "section": heading, "section_index": sec_index, "line_start": buf_start})
                buf, buf_start = [], None
            if buf_start is None:
                buf_start = line_no
            buf.append(unit)
        if buf:
            chunks.append({"text": prefix + "\n".join(buf), "section": heading, "section_index": sec_index, "line_start": buf_start})
    return chunks


def _chunk_by_sections(text: str, max_chunk_size: int) -> list[str]:
    """Backward-compatible helper (plain strings)."""
    return [c["text"] for c in chunk_note(text, _extract_title(text))]


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes() + f"|pipeline={PIPELINE_VERSION}|model={EMBEDDING_MODEL}".encode()).hexdigest()


IGNORED_DIRS = {"sources", "indexes"}


def _discover_note_files(folder: Path | None = None) -> list[Path]:
    folder = folder or _notes_folder()
    if not folder.exists():
        return []
    extensions = {".md", ".txt", ".markdown"}
    files: list[Path] = []
    for path in folder.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in extensions:
            continue
        if any(part in IGNORED_DIRS for part in path.parts):
            continue
        if path.name.startswith("_"):
            continue
        files.append(path)
    return sorted(files)


def _make_doc_id(rel_path: str, chunk_index: int) -> str:
    return f"{rel_path}::{chunk_index}"


def index_notes(folder: Path | None = None, force: bool = False) -> dict[str, Any]:
    """Reconcile the index with the notes on disk: add/refresh changed files, drop removed ones."""
    folder = folder or _notes_folder()
    collection = _get_collection()
    files = _discover_note_files(folder)

    indexed_files = indexed_chunks = skipped_files = 0
    current = set()
    for file_path in files:
        rel_path = str(file_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        current.add(rel_path)
        content = file_path.read_text(encoding="utf-8")
        file_id = _file_hash(file_path)

        existing = collection.get(where={"source_file": rel_path}, include=["metadatas"])
        if existing["ids"] and not force:
            existing_hash = existing["metadatas"][0].get("file_hash") if existing["metadatas"] else None
            if existing_hash == file_id:
                skipped_files += 1
                continue
        if existing["ids"]:
            collection.delete(ids=existing["ids"])

        title = _extract_title(content)
        chunks = chunk_note(content, title)
        if not chunks:
            continue
        parts = rel_path.split("/")
        category = parts[1] if len(parts) > 2 else "general"
        doc_type = doc_type_for(rel_path)
        collection.add(
            ids=[_make_doc_id(rel_path, i) for i in range(len(chunks))],
            documents=[c["text"] for c in chunks],
            metadatas=[
                {
                    "source_file": rel_path,
                    "category": category,
                    "doc_type": doc_type,
                    "chunk_index": i,
                    "section": c["section"][:200],
                    "section_index": c["section_index"],
                    "line_start": c["line_start"] or 0,
                    "file_hash": file_id,
                    "title": title,
                }
                for i, c in enumerate(chunks)
            ],
        )
        indexed_files += 1
        indexed_chunks += len(chunks)

    # Files deleted or renamed on disk must not keep answering queries.
    removed = 0
    all_meta = collection.get(include=["metadatas"])
    orphan_ids = [i for i, m in zip(all_meta["ids"], all_meta["metadatas"] or []) if (m or {}).get("source_file") not in current]
    if orphan_ids:
        collection.delete(ids=orphan_ids)
        removed = len(orphan_ids)

    return {
        "indexed_files": indexed_files,
        "indexed_chunks": indexed_chunks,
        "skipped_files": skipped_files,
        "removed_orphan_chunks": removed,
        "total_files": len(files),
        "pipeline_version": PIPELINE_VERSION,
    }


def index_status() -> dict[str, Any]:
    """How many files are stale / missing / orphaned right now (for the UI and MCP)."""
    collection = _get_collection()
    meta = collection.get(include=["metadatas"])["metadatas"] or []
    stored: dict[str, str] = {}
    for m in meta:
        if m:
            stored[m.get("source_file")] = m.get("file_hash")
    disk = {str(p.relative_to(PROJECT_ROOT)).replace("\\", "/"): _file_hash(p) for p in _discover_note_files()}
    return {
        "chunks": len(meta),
        "files_on_disk": len(disk),
        "stale": sorted(f for f, h in disk.items() if f in stored and stored[f] != h),
        "missing": sorted(f for f in disk if f not in stored),
        "orphans": sorted(f for f in stored if f not in disk),
        "pipeline_version": PIPELINE_VERSION,
    }


def _extract_title(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line)
        if line and not _is_rule_line(line):
            return line[:80]
    return "Untitled"


def _section_text(collection, source_file: str, section_index: int) -> str:
    sib = collection.get(where={"$and": [{"source_file": source_file}, {"section_index": section_index}]},
                         include=["documents", "metadatas"])
    pairs = sorted(zip(sib["metadatas"] or [], sib["documents"] or []), key=lambda x: x[0].get("chunk_index", 0))
    out, heading_seen = [], False
    for _meta, doc in pairs:
        if doc.startswith("[") and "]\n" in doc:
            head, body = doc.split("]\n", 1)
            if not heading_seen:
                out.append(head + "]")
                heading_seen = True
            out.append(body)
        else:
            out.append(doc)
    text = "\n".join(out)
    return text if len(text) <= SECTION_EXPAND_CHARS else text[:SECTION_EXPAND_CHARS] + " [...]"


def search_notes(
    query: str,
    top_k: int | None = None,
    category: str | None = None,
    scope: str = "all",
    expand_sections: bool = True,
    pinned_files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Semantic search. At most MAX_PER_FILE hits per file; hits expanded to their section.

    `pinned_files`: notes that must be present whatever the similarity ranking (e.g. the
    gatillo of the trade direction); their best-matching section goes first.
    """
    cfg = get_config()
    top_k = top_k or cfg["notes"]["top_k"]
    collection = _get_collection()
    where = _scope_where(category, scope)

    try:
        count = collection.count()
    except Exception:
        count = 0
    if count == 0:
        index_notes()
        count = collection.count()  # bootstrap: recount, or the first query asks for 1 result
    if count == 0:
        return []

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k * 4, count),
        where=where,
        include=["documents", "metadatas", "distances"],
    )
    items: list[dict[str, Any]] = []
    if not results["ids"] or not results["ids"][0]:
        return items

    per_file: dict[str, int] = {}
    seen_sections: set[tuple[str, int]] = set()
    for pinned in pinned_files or []:
        hit = collection.query(query_texts=[query], n_results=1, where={"source_file": pinned},
                               include=["documents", "metadatas", "distances"])
        if not hit["ids"] or not hit["ids"][0]:
            continue
        meta = hit["metadatas"][0][0]
        doc = hit["documents"][0][0]
        if expand_sections and meta.get("section_index") is not None:
            doc = _section_text(collection, pinned, meta["section_index"]) or doc
            seen_sections.add((pinned, meta.get("section_index")))
        per_file[pinned] = 1
        items.append({"id": hit["ids"][0][0], "content": doc, "source_file": pinned,
                      "category": meta.get("category"), "doc_type": meta.get("doc_type"),
                      "section": meta.get("section"), "line_start": meta.get("line_start"),
                      "title": meta.get("title"), "pinned": True,
                      "relevance": round(1 - hit["distances"][0][0], 4) if hit.get("distances") else None})
    for i, doc_id in enumerate(results["ids"][0]):
        metadata = results["metadatas"][0][i] if results["metadatas"] else {}
        source = metadata.get("source_file")
        if per_file.get(source, 0) >= MAX_PER_FILE:
            continue
        section_key = (source, metadata.get("section_index"))
        if expand_sections and section_key in seen_sections:
            continue
        distance = results["distances"][0][i] if results["distances"] else None
        document = results["documents"][0][i] if results["documents"] else ""
        if expand_sections and metadata.get("section_index") is not None:
            document = _section_text(collection, source, metadata["section_index"]) or document
            seen_sections.add(section_key)
        per_file[source] = per_file.get(source, 0) + 1
        items.append(
            {
                "id": doc_id,
                "content": document,
                "source_file": source,
                "category": metadata.get("category"),
                "doc_type": metadata.get("doc_type"),
                "section": metadata.get("section"),
                "line_start": metadata.get("line_start"),
                "title": metadata.get("title"),
                # Cosine similarity of the query to the text: topical closeness, NOT confidence.
                "relevance": round(1 - distance, 4) if distance is not None else None,
            }
        )
        if len(items) >= top_k:
            break
    return items


def pinned_for(direction: str | None) -> list[str]:
    """Notes every analysis of that direction must see (rules, not left to similarity)."""
    if direction in ("long", "short"):
        return [f"notes/order-flow/gatillo-{direction}-visual-atas.md"]
    return []


def build_search_query(
    snapshot_dict: dict[str, Any],
    context_dict: dict[str, Any] | None = None,
    verdict: dict[str, Any] | None = None,
    question: str | None = None,
) -> str:
    """Query from what was actually observed, most specific first (the embedding only sees
    the first ~128 tokens, so generic topics go last or not at all)."""
    structure = snapshot_dict.get("structure", {})
    order_flow = snapshot_dict.get("order_flow", {})
    verdict = verdict or {}
    direction = verdict.get("direction") or (context_dict or {}).get("bias")

    parts: list[str] = []
    if question:
        parts.append(question.strip()[:200])
    if direction == "long":
        parts.append("gatillo long soporte mecha abajo absorcion volumen delta rojo a verde")
    elif direction == "short":
        parts.append("gatillo short resistencia mecha arriba drenaje de delta verde a rojo")
    parts.extend(verdict.get("of_patterns") or [])
    blocked = verdict.get("blocked_reasons") or []
    if blocked:
        parts.append("invalidacion " + " ".join(str(b) for b in blocked[:2]))

    trend = str(structure.get("trend", "")).lower()
    of_bias = str(order_flow.get("bias", "")).lower()
    if trend == "bullish" or of_bias == "bullish":
        parts.append("acumulacion spring compra")
    if trend == "bearish" or of_bias == "bearish":
        parts.append("distribucion upthrust venta drenaje de delta")
    if order_flow.get("cvd_divergence"):
        parts.append(f"divergencia CVD {order_flow['cvd_divergence']}")

    if context_dict:
        wave = str(context_dict.get("wave", "")).lower()
        if wave:
            parts.append(f"onda {wave[:80]}")
        if "2" in wave:
            parts.append("fin onda 2 entrada onda 3 golden pocket")
        if "4" in wave:
            parts.append("fin onda 4 entrada onda 5")
        if "5" in wave:
            parts.append("onda 5 divergencia distribucion")
    return " ".join(str(p) for p in parts if p)
