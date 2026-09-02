"""Index and search trading notes with ChromaDB."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import chromadb
from chromadb.utils import embedding_functions

from agent.config import PROJECT_ROOT, get_config, resolve_path

COLLECTION_NAME = "trading_notes"
_collection = None
_embedding_fn = None

# Categoria de las notas destiladas de libros (notes/libros/**).
# El scope controla si entran o no en el retrieval de un analisis.
BOOKS_CATEGORY = "libros"


def _scope_where(category: str | None, scope: str | None) -> dict[str, Any] | None:
    """Traduce (category, scope) al filtro `where` de Chroma.

    scope:
      - "core"  -> tus notas (order-flow, elliott, setup-rules...), SIN libros.
      - "books" -> solo notas de libros.
      - "all"   -> todo (core + libros).
    `category` explicito tiene prioridad sobre scope.
    """
    if category:
        return {"category": category}
    scope = (scope or "all").lower()
    if scope == "core":
        return {"category": {"$ne": BOOKS_CATEGORY}}
    if scope == "books":
        return {"category": BOOKS_CATEGORY}
    return None  # all


def _get_embedding_function():
    global _embedding_fn
    if _embedding_fn is None:
        _embedding_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _embedding_fn


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


def _chunk_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []

    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start += chunk_size - overlap
    return chunks


def _chunk_by_sections(text: str, max_chunk_size: int) -> list[str]:
    """Split markdown/text by section headers (==== or ##)."""
    lines = text.splitlines()
    sections: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            block = "\n".join(current).strip()
            if block:
                sections.append(block)

    for line in lines:
        is_header = line.strip().startswith("#") or (
            len(line.strip()) >= 10 and set(line.strip()) <= {"=", "-"}
        )
        if is_header and current:
            flush()
            current = [line]
        else:
            current.append(line)
    flush()

    if not sections:
        return _chunk_text(text, max_chunk_size, 50)

    chunks: list[str] = []
    for section in sections:
        if len(section) <= max_chunk_size:
            chunks.append(section)
        else:
            chunks.extend(_chunk_text(section, max_chunk_size, 50))
    return chunks


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


IGNORED_DIRS = {"sources"}


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
    """Index all note files into ChromaDB. Returns stats."""
    cfg = get_config()
    folder = folder or _notes_folder()
    chunk_size = cfg["notes"]["chunk_size"]
    chunk_overlap = cfg["notes"]["chunk_overlap"]
    by_sections = cfg["notes"].get("chunk_by_sections", True)

    collection = _get_collection()
    files = _discover_note_files(folder)

    indexed_files = 0
    indexed_chunks = 0
    skipped_files = 0

    for file_path in files:
        rel_path = str(file_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
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

        if by_sections:
            chunks = _chunk_by_sections(content, chunk_size)
        else:
            chunks = _chunk_text(content, chunk_size, chunk_overlap)
        if not chunks:
            continue

        category = rel_path.split("/")[1] if "/" in rel_path else "general"
        ids = [_make_doc_id(rel_path, i) for i in range(len(chunks))]
        metadatas = [
            {
                "source_file": rel_path,
                "category": category,
                "chunk_index": i,
                "file_hash": file_id,
                "title": _extract_title(content),
            }
            for i in range(len(chunks))
        ]

        collection.add(ids=ids, documents=chunks, metadatas=metadatas)
        indexed_files += 1
        indexed_chunks += len(chunks)

    return {
        "indexed_files": indexed_files,
        "indexed_chunks": indexed_chunks,
        "skipped_files": skipped_files,
        "total_files": len(files),
    }


def _extract_title(content: str) -> str:
    for line in content.splitlines():
        line = line.strip()
        if line.startswith("#"):
            return re.sub(r"^#+\s*", "", line)
        if line:
            return line[:80]
    return "Untitled"


def search_notes(
    query: str,
    top_k: int | None = None,
    category: str | None = None,
    scope: str = "all",
) -> list[dict[str, Any]]:
    """Semantic search over indexed notes.

    scope = "core" | "books" | "all" (ver `_scope_where`). Default "all" para no
    cambiar el comportamiento de los buscadores explicitos (CLI/MCP `search`).
    El pipeline de analisis pasa scope="core" por defecto.
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

    results = collection.query(
        query_texts=[query],
        n_results=min(top_k, max(count, 1)),
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    items: list[dict[str, Any]] = []
    if not results["ids"] or not results["ids"][0]:
        return items

    for i, doc_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i] if results["distances"] else None
        metadata = results["metadatas"][0][i] if results["metadatas"] else {}
        document = results["documents"][0][i] if results["documents"] else ""
        items.append(
            {
                "id": doc_id,
                "content": document,
                "source_file": metadata.get("source_file"),
                "category": metadata.get("category"),
                "title": metadata.get("title"),
                "relevance": round(1 - distance, 4) if distance is not None else None,
            }
        )
    return items


def build_search_query(
    snapshot_dict: dict[str, Any],
    context_dict: dict[str, Any] | None = None,
) -> str:
    """Build a semantic query from market snapshot + Elliott context."""
    structure = snapshot_dict.get("structure", {})
    order_flow = snapshot_dict.get("order_flow", {})
    indicators = snapshot_dict.get("indicators", {})

    parts = [
        f"trend {structure.get('trend', 'neutral')}",
        f"order flow {order_flow.get('bias', 'mixed')}",
        f"volume spike {indicators.get('volume_spike', False)}",
        f"RSI {indicators.get('rsi')}",
        "elliott wave setup reglas onda impulso correctiva",
        "order flow absorption delta CVD sweep reclaim",
        "wyckoff acumulacion distribucion spring upthrust fase rango",
        "esfuerzo resultado causa efecto linea menor resistencia",
        "triple pantalla marea confluencia divergencia momentum",
        "psicologia disciplina probabilidades gestion riesgo sizing",
    ]

    trend = str(structure.get("trend", "")).lower()
    of_bias = str(order_flow.get("bias", "")).lower()
    if "up" in trend or of_bias == "bullish":
        parts.append("acumulacion spring LPS golden pocket compra")
    if "down" in trend or of_bias == "bearish":
        parts.append("distribucion upthrust UTAD LPSY venta")

    if context_dict:
        parts.extend(
            [
                f"wave {context_dict.get('wave', '')}",
                f"bias {context_dict.get('bias', '')}",
                f"POI golden pocket fibonacci",
                f"invalidation {context_dict.get('invalidation')}",
            ]
        )
        wave = str(context_dict.get("wave", "")).lower()
        if "2" in wave:
            parts.append("fin onda 2 entrada onda 3 golden pocket")
        if "4" in wave:
            parts.append("fin onda 4 entrada onda 5 divergencia CVD")
        if "5" in wave:
            parts.append("onda 5 divergencia volumen distribucion")

        of_patterns = snapshot_dict.get("order_flow", {}).get("patterns", [])
        if of_patterns:
            parts.extend(of_patterns)

    return " ".join(str(p) for p in parts if p)
