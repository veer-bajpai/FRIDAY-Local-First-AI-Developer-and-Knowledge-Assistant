"""Hybrid knowledge retrieval and long-term memory on top of the existing SQLite database.

* Lexical search uses an SQLite FTS5 index (BM25) kept in sync with ``chunks`` by triggers.
* Semantic search uses an in-memory, normalised float32 matrix of the stored Ollama embeddings.
  It is built once and rebuilt only when documents change (``bump()``), so queries do one matrix
  product instead of parsing every stored embedding.
* Results are fused with reciprocal-rank fusion, de-duplicated by content hash and returned with
  document, chunk and page attribution.

The ``chunks.embedding`` column stays JSON text, so existing databases keep working.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import dataclass
from typing import Any, Callable

try:  # numpy is optional: without it semantic ranking falls back to pure Python
    import numpy as np
except ImportError:  # pragma: no cover
    np = None

RRF_K = 60
CANDIDATES = 50
CHUNK_WORDS = 220
CHUNK_OVERLAP = 40


# ------------------------------------------------------------------ schema

SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'fact',
    source TEXT,
    embedding TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, content='chunks', content_rowid='id', tokenize='porter unicode61'
);
CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
END;
CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, content) VALUES ('delete', old.id, old.content);
    INSERT INTO chunks_fts(rowid, content) VALUES (new.id, new.content);
END;
"""


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def migrate(connection: sqlite3.Connection) -> bool:
    """Add new columns/tables in place. Returns True when FTS5 is available."""
    chunk_columns = _columns(connection, "chunks")
    for name, ddl in (("content_hash", "TEXT"), ("page", "INTEGER")):
        if name not in chunk_columns:
            connection.execute(f"ALTER TABLE chunks ADD COLUMN {name} {ddl}")
    document_columns = _columns(connection, "documents")
    for name, ddl in (("extraction", "TEXT"), ("content_hash", "TEXT")):
        if name not in document_columns:
            connection.execute(f"ALTER TABLE documents ADD COLUMN {name} {ddl}")
    connection.executescript(SCHEMA)
    connection.execute("CREATE INDEX IF NOT EXISTS idx_chunks_document ON chunks(document_id)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_chunks_hash ON chunks(content_hash)")
    # Backfill hashes before the FTS triggers exist, so the update does not touch the index.
    connection.execute("DROP TRIGGER IF EXISTS chunks_au")
    missing = connection.execute("SELECT id, content FROM chunks WHERE content_hash IS NULL").fetchall()
    connection.executemany("UPDATE chunks SET content_hash = ? WHERE id = ?",
                           [(content_hash(row[1]), row[0]) for row in missing])
    try:
        connection.executescript(FTS_SCHEMA)
    except sqlite3.OperationalError:
        return False
    # For an external-content FTS table COUNT(*) reads the source table; the docsize shadow
    # table holds one row per indexed chunk, so compare against it.
    indexed = connection.execute("SELECT COUNT(*) FROM chunks_fts_docsize").fetchone()[0]
    total = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    if indexed != total:
        connection.execute("INSERT INTO chunks_fts(chunks_fts) VALUES ('rebuild')")
    return True


# ------------------------------------------------------------------ chunking

def content_hash(text: str) -> str:
    return hashlib.sha1(re.sub(r"\s+", " ", text.strip().lower()).encode("utf-8")).hexdigest()


def chunk_text(text: str, words: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware chunks of roughly ``words`` words with a small overlap."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    for paragraph in paragraphs:
        tokens = paragraph.split()
        if len(current) + len(tokens) > words and current:
            chunks.append(" ".join(current))
            current = current[-overlap:] if overlap else []
        while len(tokens) > words:
            room = words - len(current)
            current += tokens[:room]
            tokens = tokens[room:]
            chunks.append(" ".join(current))
            current = current[-overlap:] if overlap else []
        current += tokens
    if current and (not chunks or " ".join(current) != chunks[-1]):
        chunks.append(" ".join(current))
    return chunks


# ------------------------------------------------------------------ vector index

@dataclass
class _Matrix:
    version: int
    ids: list[int]
    documents: list[str]
    vectors: Any  # np.ndarray (n, d) normalised, or list of lists
    dimension: int


class VectorIndex:
    """Lazily built, version-invalidated matrix of chunk embeddings."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._version = 0
        self._matrix: _Matrix | None = None

    def bump(self) -> None:
        with self._lock:
            self._version += 1

    @property
    def version(self) -> int:
        return self._version

    def _build(self, connection: sqlite3.Connection) -> _Matrix:
        rows = connection.execute(
            """SELECT chunks.id, chunks.document_id, chunks.embedding FROM chunks
               JOIN documents ON documents.id = chunks.document_id
               WHERE documents.status = 'ready' AND chunks.embedding IS NOT NULL"""
        ).fetchall()
        parsed: list[tuple[int, str, list[float]]] = []
        dims: dict[int, int] = {}
        for chunk_id, document_id, raw in rows:
            try:
                vector = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(vector, list) and vector:
                parsed.append((chunk_id, document_id, vector))
                dims[len(vector)] = dims.get(len(vector), 0) + 1
        dimension = max(dims, key=lambda size: dims[size]) if dims else 0  # ignore vectors from an old embed model
        parsed = [item for item in parsed if len(item[2]) == dimension]
        ids = [item[0] for item in parsed]
        documents = [item[1] for item in parsed]
        if np is not None and parsed:
            matrix = np.asarray([item[2] for item in parsed], dtype=np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            norms[norms == 0] = 1.0
            vectors = matrix / norms
        else:
            vectors = [_normalise(item[2]) for item in parsed]
        return _Matrix(self._version, ids, documents, vectors, dimension)

    def search(self, connection: sqlite3.Connection, query: list[float], limit: int,
               allowed_documents: set[str] | None = None) -> list[tuple[int, float]]:
        with self._lock:
            if self._matrix is None or self._matrix.version != self._version:
                self._matrix = self._build(connection)
            matrix = self._matrix
        if not matrix.ids or len(query) != matrix.dimension:
            return []
        if np is not None:
            q = np.asarray(query, dtype=np.float32)
            norm = float(np.linalg.norm(q)) or 1.0
            scores = matrix.vectors @ (q / norm)
            if allowed_documents is not None:
                mask = np.fromiter((d in allowed_documents for d in matrix.documents), bool, len(matrix.documents))
                scores = np.where(mask, scores, -2.0)
            take = min(limit, len(scores))
            top = np.argpartition(-scores, take - 1)[:take]
            ranked = sorted(((matrix.ids[i], float(scores[i])) for i in top), key=lambda x: -x[1])
        else:
            q = _normalise(query)
            ranked = sorted(
                ((matrix.ids[i], sum(a * b for a, b in zip(q, v))) for i, v in enumerate(matrix.vectors)
                 if allowed_documents is None or matrix.documents[i] in allowed_documents),
                key=lambda x: -x[1])[:limit]
        return [(chunk_id, score) for chunk_id, score in ranked if score > -1.5]


def _normalise(vector: list[float]) -> list[float]:
    norm = sum(x * x for x in vector) ** 0.5 or 1.0
    return [x / norm for x in vector]


INDEX = VectorIndex()


# ------------------------------------------------------------------ lexical search

def fts_query(text: str) -> str:
    terms = [t for t in re.findall(r"\w+", text.lower()) if len(t) > 1][:24]
    return " OR ".join(f'"{t}"' for t in terms)


def lexical_search(connection: sqlite3.Connection, query: str, limit: int,
                   allowed_documents: set[str] | None = None) -> list[tuple[int, float]]:
    match = fts_query(query)
    if not match:
        return []
    try:
        rows = connection.execute(
            """SELECT chunks.id, chunks.document_id, bm25(chunks_fts) AS rank FROM chunks_fts
               JOIN chunks ON chunks.id = chunks_fts.rowid
               JOIN documents ON documents.id = chunks.document_id
               WHERE chunks_fts MATCH ? AND documents.status = 'ready'
               ORDER BY rank LIMIT ?""",
            (match, limit * 3 if allowed_documents is not None else limit),
        ).fetchall()
    except sqlite3.OperationalError:
        return _lexical_fallback(connection, query, limit, allowed_documents)
    results = [(row[0], -float(row[2])) for row in rows
               if allowed_documents is None or row[1] in allowed_documents]
    return results[:limit]


def _lexical_fallback(connection, query, limit, allowed_documents):
    wanted = set(re.findall(r"\w+", query.lower()))
    scored = []
    for chunk_id, document_id, content in connection.execute(
            """SELECT chunks.id, chunks.document_id, chunks.content FROM chunks
               JOIN documents ON documents.id = chunks.document_id WHERE documents.status = 'ready'"""):
        if allowed_documents is not None and document_id not in allowed_documents:
            continue
        overlap = len(wanted & set(re.findall(r"\w+", content.lower())))
        if overlap:
            scored.append((chunk_id, overlap / max(1, len(wanted))))
    return sorted(scored, key=lambda x: -x[1])[:limit]


# ------------------------------------------------------------------ hybrid retrieval

def hybrid_rank(lexical: list[tuple[int, float]], semantic: list[tuple[int, float]]) -> list[dict[str, Any]]:
    """Reciprocal-rank fusion plus a display score in [0, 1]."""
    fused: dict[int, dict[str, Any]] = {}
    top_lex = lexical[0][1] if lexical else 1.0
    for rank, (chunk_id, score) in enumerate(lexical):
        item = fused.setdefault(chunk_id, {"id": chunk_id, "rrf": 0.0, "lexical": 0.0, "semantic": 0.0})
        item["rrf"] += 1.0 / (RRF_K + rank + 1)
        item["lexical"] = score / top_lex if top_lex > 0 else 0.0
    for rank, (chunk_id, score) in enumerate(semantic):
        item = fused.setdefault(chunk_id, {"id": chunk_id, "rrf": 0.0, "lexical": 0.0, "semantic": 0.0})
        item["rrf"] += 1.0 / (RRF_K + rank + 1)
        item["semantic"] = max(0.0, score)
    for item in fused.values():
        has_lex, has_sem = item["lexical"] > 0, item["semantic"] > 0
        item["method"] = "hybrid" if has_lex and has_sem else "lexical" if has_lex else "semantic"
        item["score"] = (0.5 * item["lexical"] + 0.5 * item["semantic"]) if semantic else item["lexical"]
    return sorted(fused.values(), key=lambda x: (-x["rrf"], -x["score"]))


def retrieve(connection: sqlite3.Connection, query: str, query_vector: list[float] | None, limit: int,
             collection_id: str | None = None, max_distance: float | None = None,
             document_ids: list[str] | None = None) -> list[dict[str, Any]]:
    allowed: set[str] | None = None
    if collection_id:
        allowed = {row[0] for row in connection.execute(
            "SELECT document_id FROM collection_documents WHERE collection_id = ?", (collection_id,))}
    if document_ids:
        allowed = set(document_ids) if allowed is None else allowed & set(document_ids)
    if allowed is not None and not allowed:
        return []

    lexical = lexical_search(connection, query, CANDIDATES, allowed)
    semantic = INDEX.search(connection, query_vector, CANDIDATES, allowed) if query_vector else []
    ranked = hybrid_rank(lexical, semantic)
    if not ranked:
        return []

    ids = [item["id"] for item in ranked[: CANDIDATES * 2]]
    placeholders = ",".join("?" * len(ids))
    rows = {row["id"]: row for row in connection.execute(
        f"""SELECT chunks.id, chunks.document_id, chunks.chunk_index, chunks.content, chunks.page,
                   chunks.content_hash, documents.name, documents.file_type, documents.extraction
            FROM chunks JOIN documents ON documents.id = chunks.document_id
            WHERE chunks.id IN ({placeholders})""", ids)}

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        row = rows.get(item["id"])
        if row is None:
            continue
        fingerprint = row["content_hash"] or content_hash(row["content"])
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        score = max(0.0, min(1.0, item["score"]))
        distance = round(1 - score, 4)
        if max_distance is not None and distance > max_distance:
            continue
        snippet = re.sub(r"\s+", " ", row["content"]).strip()
        results.append({
            "document_id": row["document_id"],
            "chunk_id": row["id"],
            "title": row["name"],
            "name": row["name"],
            "chunk": row["chunk_index"],
            "page": row["page"],
            "file_type": row["file_type"],
            "extraction": row["extraction"],
            "distance": distance,
            "score": round(score, 4),
            "method": item["method"],
            "snippet": snippet[:240],
            "content": snippet,
        })
        if len(results) >= limit:
            break
    return results


def source_label(source: dict[str, Any]) -> str:
    page = f" p.{source['page']}" if source.get("page") else ""
    return f"{source['title']}{page} / chunk {source['chunk']}"


# ------------------------------------------------------------------ long-term memory

MEMORY_KINDS = {"fact", "preference", "project", "instruction"}


def memory_view(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "content": row["content"], "kind": row["kind"], "source": row["source"],
            "created_at": row["created_at"], "updated_at": row["updated_at"]}


STOPWORDS = set("""a an and are as at be but by can could did do does for from had has have how i if in
into is it its me my of on or our should so that the their them then there these they this to was
we were what when where which who why will with would you your about any some just like""".split())


def _keywords(text: str) -> set[str]:
    return {t for t in re.findall(r"\w+", text.lower()) if len(t) > 2 and t not in STOPWORDS}


def relevant_memories(connection: sqlite3.Connection, query: str, query_vector: list[float] | None,
                      limit: int = 4, min_score: float = 0.35) -> list[dict[str, Any]]:
    """Small table: score every memory lexically and semantically (no index needed)."""
    wanted = _keywords(query)
    scored = []
    for row in connection.execute("SELECT * FROM memories"):
        words = _keywords(row["content"])
        lexical = len(wanted & words) / max(1, len(wanted)) if wanted else 0.0
        semantic = 0.0
        if query_vector and row["embedding"]:
            try:
                stored = json.loads(row["embedding"])
                if len(stored) == len(query_vector):
                    a, b = _normalise(stored), _normalise(query_vector)
                    semantic = sum(x * y for x, y in zip(a, b))
            except (TypeError, ValueError):
                pass
        score = max(lexical, semantic) if row["kind"] != "instruction" else max(lexical, semantic, min_score)
        if score >= min_score:
            scored.append((score, row))
    scored.sort(key=lambda item: -item[0])
    return [{**memory_view(row), "score": round(score, 3)} for score, row in scored[:limit]]


REMEMBER_PATTERN = re.compile(r"^\s*(?:/remember|remember(?:\s+that)?\s*:)\s*(.+)$", re.I | re.S)


def parse_remember(text: str) -> str | None:
    match = REMEMBER_PATTERN.match(text)
    return match.group(1).strip() if match else None


EmbedFn = Callable[[str], Any]

