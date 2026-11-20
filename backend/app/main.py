import asyncio
from contextlib import asynccontextmanager
import json
import logging
import os
import re
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_ROOT = next(
    (
        parent
        for parent in Path(__file__).resolve().parents
        if (parent / "friday_agent" / "__init__.py").is_file()
    ),
    None,
)
if PROJECT_ROOT is None:
    raise RuntimeError("Could not locate the FRIDAY workspace root.")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from . import knowledge
from .extraction import ExtractionError, capabilities as extraction_capabilities, extract, is_supported
from .observability import RequestLogMiddleware, configure_logging, request_id_var
from .security import SecurityMiddleware
from .agent_routes import router as agent_router, start_mcp_servers, stop_mcp_servers
from .agent.router import router as coding_agent_router
from friday_agent.config import (
    DEFAULT_EMBED_MODEL,
    DEFAULT_MODEL,
    OLLAMA_KEEP_ALIVE,
    OLLAMA_TIMEOUT_SECONDS,
)
from friday_agent.llm import installed_models


configure_logging()
log = logging.getLogger("friday.api")

DATA_DIR = Path(os.getenv("FRIDAY_DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "friday.db"

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://localhost:11434"
).rstrip("/")
OLLAMA_TIMEOUT = httpx.Timeout(OLLAMA_TIMEOUT_SECONDS, connect=5.0)
MAX_UPLOAD_BYTES = int(os.getenv("FRIDAY_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024)))
ollama_client: httpx.AsyncClient | None = None


# ============================================================
# APPLICATION
# ============================================================

@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    try:
        await start_mcp_servers()
        await startup()
        yield
    finally:
        await stop_mcp_servers()
        await shutdown()


app = FastAPI(title="Friday API", version="1.0.0", lifespan=lifespan)
app.include_router(agent_router)
app.include_router(coding_agent_router)


# ============================================================
# CORS
# ============================================================

# Local development + deployed Vercel frontend
default_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://friday-local-first-ai-knowledge-ass.vercel.app",
]

configured_origins = os.getenv("FRIDAY_ALLOWED_ORIGINS", "")

if configured_origins:
    allow_origins = [
        origin.strip()
        for origin in configured_origins.split(",")
        if origin.strip()
    ]
else:
    allow_origins = default_origins.copy()

# Always keep the known FRIDAY frontend origins allowed.
for origin in default_origins:
    if origin not in allow_origins:
        allow_origins.append(origin)


# Security runs inside CORS so that rejected responses still carry CORS headers.
app.add_middleware(
    SecurityMiddleware,
    max_upload_bytes=int(os.getenv("FRIDAY_MAX_UPLOAD_BYTES", str(25 * 1024 * 1024))) + 1024 * 1024,
)

app.add_middleware(RequestLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Friday-Token"],
)


from fastapi import Request as _Request
from fastapi.responses import JSONResponse as _JSONResponse


@app.exception_handler(Exception)
async def unhandled_error(request: _Request, exc: Exception) -> _JSONResponse:
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return _JSONResponse(
        {"detail": "FRIDAY hit an internal error. Check the backend log.", "request_id": request_id_var.get()},
        status_code=500,
    )


@app.exception_handler(sqlite3.OperationalError)
async def database_busy(request: _Request, exc: sqlite3.OperationalError) -> _JSONResponse:
    log.error("database error on %s: %s", request.url.path, exc)
    locked = "locked" in str(exc).lower() or "busy" in str(exc).lower()
    return _JSONResponse(
        {"detail": "The database is busy, retry shortly." if locked else "Database error.",
         "request_id": request_id_var.get()},
        status_code=503 if locked else 500,
    )


# ============================================================
# REQUEST MODELS
# ============================================================

class SearchRequest(BaseModel):
    query: str = Field(
        min_length=1,
        max_length=1000
    )
    k: int = Field(
        default=5,
        ge=1,
        le=20
    )
    collection_id: str | None = None
    document_ids: list[str] | None = Field(default=None, max_length=50)


class TitleRequest(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=120
    )


class SettingsUpdate(BaseModel):
    embed_model: str | None = Field(default=None, max_length=120, pattern=r"^[A-Za-z0-9_.:/@-]+$")
    max_distance: float | None = Field(
        default=None,
        ge=0,
        le=2
    )


# ============================================================
# HELPERS
# ============================================================

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class ClosingConnection(sqlite3.Connection):
    """``with db() as connection`` commits (or rolls back) and then closes the connection.

    The stock sqlite3 context manager only commits, which leaked one open connection per call.
    """

    def __exit__(self, exc_type, exc, traceback):
        try:
            return super().__exit__(exc_type, exc, traceback)
        finally:
            self.close()


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    connection = sqlite3.connect(DB_PATH, timeout=15, factory=ClosingConnection)

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )
    connection.execute("PRAGMA busy_timeout = 15000")

    return connection


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

def initialize() -> None:
    UPLOAD_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    with db() as connection:
        try:
            connection.execute("PRAGMA journal_mode = WAL")  # readers don't block the writer
        except sqlite3.OperationalError:
            pass
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                file_path TEXT NOT NULL,
                file_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL
                    REFERENCES documents(id)
                    ON DELETE CASCADE,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                embedding TEXT
            );

            CREATE TABLE IF NOT EXISTS activity (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration_ms REAL
            );

            CREATE TABLE IF NOT EXISTS collections (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS collection_documents (
                collection_id TEXT
                    REFERENCES collections(id)
                    ON DELETE CASCADE,
                document_id TEXT
                    REFERENCES documents(id)
                    ON DELETE CASCADE,
                PRIMARY KEY(
                    collection_id,
                    document_id
                )
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )

        defaults = {"embed_model": DEFAULT_EMBED_MODEL, "max_distance": "2"}

        connection.executemany(
            """
            INSERT OR IGNORE INTO settings(key, value)
            VALUES (?, ?)
            """,
            defaults.items()
        )
        knowledge.migrate(connection)
async def startup() -> None:
    global ollama_client
    initialize()

    ollama_client = httpx.AsyncClient(timeout=OLLAMA_TIMEOUT)
    with db() as connection:
        stuck = [row["id"] for row in connection.execute(
            "SELECT id FROM documents WHERE status = 'processing'"
        )]
    for document_id in stuck:  # ingestion interrupted by a previous shutdown: resume it
        asyncio.create_task(ingest(document_id))
    if stuck:
        log.info("re-queued %d interrupted document ingestion(s)", len(stuck))
    log.info("FRIDAY API started (data dir %s)", DATA_DIR.resolve())


async def shutdown() -> None:
    global ollama_client
    if ollama_client is not None:
        await ollama_client.aclose()
        ollama_client = None


def get_ollama_client() -> httpx.AsyncClient:
    global ollama_client
    if ollama_client is None:
        ollama_client = httpx.AsyncClient(timeout=OLLAMA_TIMEOUT)
    return ollama_client


# ============================================================
# SETTINGS
# ============================================================

def get_settings() -> dict[str, Any]:
    with db() as connection:
        values = {
            row["key"]: row["value"]
            for row in connection.execute(
                "SELECT key, value FROM settings"
            )
        }

    return {
        "embed_model": values.get("embed_model", DEFAULT_EMBED_MODEL),
        "max_distance": float(values.get("max_distance", "2")),
    }


# ============================================================
# ACTIVITY
# ============================================================

def activity(
    kind: str,
    title: str,
    detail: str = "",
    status: str = "success",
    duration_ms: float | None = None
) -> None:

    with db() as connection:
        connection.execute(
            """
            INSERT INTO activity(
                type,
                title,
                detail,
                status,
                created_at,
                duration_ms
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                kind,
                title,
                detail,
                status,
                timestamp(),
                duration_ms
            )
        )


# ============================================================
# DOCUMENT CHUNKING
# ============================================================

def split_chunks(text: str) -> list[str]:
    return knowledge.chunk_text(text) or [""]


def lexical_score(
    query: str,
    content: str
) -> float:

    wanted = set(
        re.findall(
            r"\w+",
            query.lower()
        )
    )

    available = set(
        re.findall(
            r"\w+",
            content.lower()
        )
    )

    return len(
        wanted & available
    ) / max(
        1,
        len(wanted)
    )


# ============================================================
# OLLAMA EMBEDDINGS
# ============================================================

async def embedding(
    text: str,
    model: str
) -> list[float] | None:

    try:
        response = await get_ollama_client().post(
            f"{OLLAMA_URL}/api/embeddings",
            json={
                "model": model,
                "prompt": text
            }
        )

        response.raise_for_status()

        return response.json().get(
            "embedding"
        )

    except Exception:
        return None


# ============================================================
# DOCUMENT HELPERS
# ============================================================

def document_view(
    row: sqlite3.Row
) -> dict[str, Any]:

    with db() as connection:
        count = connection.execute(
            """
            SELECT COUNT(*)
            FROM chunks
            WHERE document_id = ?
            """,
            (row["id"],)
        ).fetchone()[0]

    return {
        "id": row["id"],
        "name": row["name"],
        "type": row["file_type"].upper(),
        "size": row["size_bytes"],
        "chunks": count,
        "status": row["status"],
        "error": row["error"],
    }


# ============================================================
# DOCUMENT INGESTION
# ============================================================

INGESTING: set[str] = set()


async def ingest(
    document_id: str
) -> None:
    if document_id in INGESTING:  # never run two ingestions of the same document at once
        return
    INGESTING.add(document_id)
    try:
        await _ingest(document_id)
    finally:
        INGESTING.discard(document_id)


async def _ingest(document_id: str) -> None:

    started = time.perf_counter()

    try:
        with db() as connection:
            document = connection.execute(
                """
                SELECT *
                FROM documents
                WHERE id = ?
                """,
                (document_id,)
            ).fetchone()

        if not document:
            return

        path = Path(document["file_path"])
        extracted = await extract(path, document["file_type"])
        if not extracted.text.strip():
            raise ExtractionError(
                "No text could be extracted from this file. "
                + (" ".join(extracted.warnings) or "It may be empty or image-only.")
            )

        embed_model = get_settings()["embed_model"]
        rows: list[tuple[int | None, str, str]] = []
        seen_hashes: set[str] = set()
        for page, page_text in extracted.pages:
            for content in knowledge.chunk_text(page_text):
                digest = knowledge.content_hash(content)
                if digest in seen_hashes:
                    continue  # identical chunk already stored for this document
                seen_hashes.add(digest)
                rows.append((page, content, digest))

        vectors = [await embedding(content, embed_model) for _, content, _ in rows]

        with db() as connection:
            connection.execute("DELETE FROM chunks WHERE document_id = ?", (document_id,))
            connection.executemany(
                """
                INSERT INTO chunks(document_id, chunk_index, content, embedding, content_hash, page)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (document_id, index, content, json.dumps(vector) if vector else None, digest, page)
                    for index, ((page, content, digest), vector) in enumerate(zip(rows, vectors))
                ],
            )
            connection.execute(
                """
                UPDATE documents
                SET status = 'ready', error = ?, extraction = ?, content_hash = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    "; ".join(extracted.warnings) or None,
                    extracted.method,
                    knowledge.content_hash(extracted.text),
                    timestamp(),
                    document_id,
                ),
            )
        knowledge.INDEX.bump()
        embedded = sum(1 for vector in vectors if vector)
        if rows and not embedded:
            log.warning("document %s stored without embeddings (embedding model unavailable)", document_id)

        activity(
            "ingest",
            "Document ready",
            document["name"],
            duration_ms=(
                time.perf_counter() - started
            ) * 1000
        )

    except Exception as exc:

        with db() as connection:
            connection.execute(
                """
                UPDATE documents
                SET
                    status = 'failed',
                    error = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    str(exc),
                    timestamp(),
                    document_id
                )
            )

        activity(
            "ingest",
            "Document ingestion failed",
            str(exc),
            "error",
            (
                time.perf_counter() - started
            ) * 1000
        )


# ============================================================
# RETRIEVAL
# ============================================================

def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


async def retrieve(
    query: str,
    limit: int,
    collection_id: str | None = None,
    max_distance: float | None = None,
    document_ids: list[str] | None = None,
    query_vector: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Hybrid FTS5 (BM25) + vector-index retrieval with source attribution."""
    if query_vector is None:
        query_vector = await embedding(query, get_settings()["embed_model"])
    with db() as connection:
        return knowledge.retrieve(
            connection, query, query_vector, limit, collection_id, max_distance, document_ids
        )


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe: the process is up. Use /api/health for dependency checks."""
    return {
        "status": "ok",
        "service": "friday-api"
    }


@app.get("/api/health")
async def deep_health() -> dict[str, Any]:
    """Readiness: database, Ollama and configured models, agent store, optional integrations."""
    from .agent_routes import agent_health

    checks: dict[str, Any] = {}
    started = time.perf_counter()
    try:
        with db() as connection:
            connection.execute("SELECT 1").fetchone()
            documents = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            failed = connection.execute("SELECT COUNT(*) FROM documents WHERE status = 'failed'").fetchone()[0]
            fts = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'chunks_fts'"
            ).fetchone() is not None
        checks["database"] = {"status": "ok", "documents": documents, "failed_documents": failed,
                              "fulltext_index": fts, "path": str(DB_PATH)}
    except sqlite3.Error as exc:
        checks["database"] = {"status": "error", "error": str(exc)}

    current = get_settings() if checks["database"]["status"] == "ok" else {}
    models = await installed_models(force=True)
    if models is None:
        checks["ollama"] = {"status": "error", "error": f"Cannot reach Ollama at {OLLAMA_URL}"}
    else:
        def has(name: str) -> bool:
            return any(name == m or f"{name}:latest" == m for m in models)
        agent_model_ok = has(DEFAULT_MODEL)
        embed_ok = has(current.get("embed_model", DEFAULT_EMBED_MODEL))
        checks["ollama"] = {
            "status": "ok" if agent_model_ok and embed_ok else "degraded",
            "models": len(models),
            "agent_model": {"name": DEFAULT_MODEL, "installed": agent_model_ok},
            "embed_model": {"name": current.get("embed_model"), "installed": embed_ok},
        }
    checks["agent"] = agent_health()
    checks["extraction"] = {"status": "ok", **extraction_capabilities()}

    states = [check.get("status") for check in checks.values()]
    overall = "error" if checks["database"]["status"] == "error" else (
        "degraded" if any(state in {"error", "degraded"} for state in states) else "ok")
    return {"status": overall, "checks": checks, "took_ms": round((time.perf_counter() - started) * 1000, 1)}


@app.get("/")
def root() -> dict[str, str]:

    return {
        "service": "friday-api",
        "status": "ok",
        "docs": "/docs"
    }


# ============================================================
# SETTINGS API
# ============================================================

@app.get("/api/settings")
def settings() -> dict[str, Any]:

    return get_settings()


@app.put("/api/settings")
async def update_settings(
    request: SettingsUpdate
) -> dict[str, Any]:
    installed = await installed_models(force=True)
    if installed is not None:  # only validate when Ollama can be asked
        for field_name in ("embed_model",):
            value = getattr(request, field_name)
            if value and not any(value == m or f"{value}:latest" == m for m in installed):
                raise HTTPException(422, f"Model '{value}' is not installed. Run: ollama pull {value}")

    with db() as connection:

        connection.executemany(
            """
            INSERT OR REPLACE INTO settings(
                key,
                value
            )
            VALUES (?, ?)
            """,
            [
                (
                    key,
                    str(value)
                )
                for key, value
                in request.model_dump(
                    exclude_none=True
                ).items()
            ]
        )

    activity(
        "settings",
        "Settings updated"
    )

    return get_settings()


# ============================================================
# DOCUMENTS
# ============================================================

@app.get("/api/documents")
def list_documents() -> list[dict[str, Any]]:

    with db() as connection:

        rows = connection.execute(
            """
            SELECT *
            FROM documents
            ORDER BY created_at DESC
            """
        ).fetchall()

    return [
        document_view(row)
        for row in rows
    ]


@app.post(
    "/api/documents/upload",
    status_code=202
)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
) -> dict[str, Any]:

    name = Path(file.filename or "untitled.txt").name
    file_type = Path(name).suffix.lower().lstrip(".") or "txt"
    if not is_supported(file_type):
        raise HTTPException(
            415,
            f"Unsupported file type '.{file_type}'. Supported: PDF, text/Markdown/code, "
            "DOCX, PPTX, XLSX, CSV, JSON, HTML and images (OCR / vision model).",
        )

    content_parts: list[bytes] = []
    content_size = 0
    while chunk := await file.read(1024 * 1024):
        content_size += len(chunk)
        if content_size > MAX_UPLOAD_BYTES:
            raise HTTPException(
                413,
                f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB upload limit"
            )
        content_parts.append(chunk)
    content = b"".join(content_parts)
    if not content:
        raise HTTPException(400, "The uploaded file is empty")

    document_id = str(
        uuid.uuid4()
    )

    path = (
        UPLOAD_DIR /
        f"{document_id}-{name}"
    )

    try:
        path.write_bytes(content)
    except OSError as exc:
        raise HTTPException(500, "Could not save uploaded file") from exc

    created = timestamp()

    with db() as connection:

        connection.execute(
            """
            INSERT INTO documents(
                id, name, file_path, file_type, size_bytes, status, error, created_at, updated_at
            )
            VALUES (
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?,
                ?
            )
            """,
            (
                document_id,
                name,
                str(path),
                file_type,
                len(content),
                "processing",
                None,
                created,
                created,
            )
        )

        row = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ?
            """,
            (document_id,)
        ).fetchone()

    background_tasks.add_task(
        ingest,
        document_id
    )

    activity(
        "upload",
        "Document uploaded",
        name,
        "processing"
    )

    return document_view(row)


@app.get(
    "/api/documents/{document_id}"
)
def get_document(
    document_id: str
) -> dict[str, Any]:

    with db() as connection:

        row = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ?
            """,
            (document_id,)
        ).fetchone()

    if not row:
        raise HTTPException(
            404,
            "Document not found"
        )

    return document_view(row)


@app.post("/api/documents/{document_id}/reprocess", status_code=202)
async def reprocess_document(document_id: str, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """Re-extract and re-index a document (e.g. after installing OCR or a vision model)."""
    with db() as connection:
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Document not found")
        if document_id in INGESTING:
            raise HTTPException(409, "This document is already being processed")
        connection.execute(
            "UPDATE documents SET status = 'processing', error = NULL, updated_at = ? WHERE id = ?",
            (timestamp(), document_id),
        )
        row = connection.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
    background_tasks.add_task(ingest, document_id)
    return document_view(row)


@app.delete(
    "/api/documents/{document_id}"
)
def delete_document(
    document_id: str
) -> dict[str, bool]:

    with db() as connection:

        row = connection.execute(
            """
            SELECT *
            FROM documents
            WHERE id = ?
            """,
            (document_id,)
        ).fetchone()

        if not row:
            raise HTTPException(
                404,
                "Document not found"
            )

    try:
        Path(row["file_path"]).unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(500, "Could not remove the uploaded file") from exc

    with db() as connection:
        connection.execute(
            "DELETE FROM documents WHERE id = ?",
            (document_id,)
        )
    knowledge.INDEX.bump()

    activity(
        "delete",
        "Document deleted",
        row["name"]
    )

    return {
        "deleted": True
    }


# ============================================================
# SEARCH
# ============================================================

@app.post("/api/search")
async def search(
    request: SearchRequest
) -> dict[str, Any]:

    started = time.perf_counter()

    current = get_settings()

    return {
        "query": request.query,
        "results": await retrieve(
            request.query,
            request.k,
            request.collection_id,
            current.get("max_distance"),
            request.document_ids,
        ),
        "took_ms": round(
            (
                time.perf_counter()
                - started
            ) * 1000,
            2
        ),
    }


# ============================================================
# ACTIVITY API
# ============================================================

@app.get("/api/activity")
def get_activity(
    limit: int = 50
) -> list[dict[str, Any]]:

    with db() as connection:

        rows = connection.execute(
            """
            SELECT *
            FROM activity
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                max(
                    1,
                    min(
                        limit,
                        200
                    )
                ),
            )
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# COLLECTIONS
# ============================================================

@app.get("/api/collections")
def list_collections() -> list[dict[str, Any]]:

    with db() as connection:

        rows = connection.execute(
            """
            SELECT
                collections.*,
                COUNT(
                    collection_documents.document_id
                ) AS document_count
            FROM collections
            LEFT JOIN collection_documents
                ON collections.id =
                   collection_documents.collection_id
            GROUP BY collections.id
            ORDER BY name
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


@app.post("/api/collections")
def create_collection(
    request: TitleRequest
) -> dict[str, Any]:

    collection = {
        "id": str(uuid.uuid4()),
        "name": request.title,
        "created_at": timestamp(),
    }

    with db() as connection:

        connection.execute(
            """
            INSERT INTO collections(id, name, created_at)
            VALUES (?, ?, ?)
            """,
            tuple(
                collection.values()
            )
        )

    return {
        **collection,
        "document_count": 0
    }


@app.patch(
    "/api/collections/{collection_id}"
)
def rename_collection(
    collection_id: str,
    request: TitleRequest
) -> dict[str, str]:

    with db() as connection:

        cursor = connection.execute(
            """
            UPDATE collections
            SET name = ?
            WHERE id = ?
            """,
            (
                request.title,
                collection_id
            )
        )

    if cursor.rowcount == 0:

        raise HTTPException(
            404,
            "Collection not found"
        )

    return {
        "id": collection_id,
        "name": request.title
    }


@app.delete(
    "/api/collections/{collection_id}"
)
def delete_collection(
    collection_id: str
) -> dict[str, bool]:

    with db() as connection:

        cursor = connection.execute(
            """
            DELETE FROM collections
            WHERE id = ?
            """,
            (collection_id,)
        )

    if cursor.rowcount == 0:

        raise HTTPException(
            404,
            "Collection not found"
        )

    return {
        "deleted": True
    }


@app.post(
    "/api/collections/{collection_id}/documents/{document_id}"
)
def add_to_collection(
    collection_id: str,
    document_id: str
) -> dict[str, bool]:

    with db() as connection:

        collection = connection.execute(
            "SELECT 1 FROM collections WHERE id = ?",
            (collection_id,)
        ).fetchone()
        document = connection.execute(
            "SELECT 1 FROM documents WHERE id = ?",
            (document_id,)
        ).fetchone()

        if not collection:
            raise HTTPException(404, "Collection not found")
        if not document:
            raise HTTPException(404, "Document not found")

        connection.execute(
            """
            INSERT OR IGNORE INTO collection_documents
            VALUES (?, ?)
            """,
            (
                collection_id,
                document_id
            )
        )

    return {
        "added": True
    }


@app.delete(
    "/api/collections/{collection_id}/documents/{document_id}"
)
def remove_from_collection(
    collection_id: str,
    document_id: str
) -> dict[str, bool]:

    with db() as connection:

        connection.execute(
            """
            DELETE FROM collection_documents
            WHERE collection_id = ?
              AND document_id = ?
            """,
            (
                collection_id,
                document_id
            )
        )

    return {
        "removed": True
    }


# ============================================================
# CHAT
# ============================================================

# ============================================================
# LONG-TERM MEMORY
# ============================================================

class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    kind: str = Field(default="fact", pattern="^(fact|preference|project|instruction)$")
    source: str | None = Field(default=None, max_length=200)


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: str | None = Field(default=None, pattern="^(fact|preference|project|instruction)$")


async def store_memory(content: str, kind: str = "fact", source: str | None = None) -> dict[str, Any]:
    vector = await embedding(content, get_settings()["embed_model"])
    created = timestamp()
    with db() as connection:
        existing = connection.execute(
            "SELECT * FROM memories WHERE lower(content) = lower(?)", (content,)
        ).fetchone()
        if existing:
            return knowledge.memory_view(existing)
        memory_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO memories(id, content, kind, source, embedding, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (memory_id, content, kind, source, json.dumps(vector) if vector else None, created, created),
        )
        row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    activity("memory", "Memory saved", content[:80])
    return knowledge.memory_view(row)


@app.get("/api/memories")
async def list_memories(q: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    if q:
        vector = await embedding(q, get_settings()["embed_model"])
        with db() as connection:
            return knowledge.relevant_memories(connection, q, vector, limit=min(limit, 50), min_score=0.0)
    with db() as connection:
        rows = connection.execute(
            "SELECT * FROM memories ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 500)),)
        ).fetchall()
    return [knowledge.memory_view(row) for row in rows]


@app.post("/api/memories", status_code=201)
async def create_memory(request: MemoryCreate) -> dict[str, Any]:
    return await store_memory(request.content.strip(), request.kind, request.source or "user")


@app.patch("/api/memories/{memory_id}")
async def update_memory(memory_id: str, request: MemoryUpdate) -> dict[str, Any]:
    with db() as connection:
        row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Memory not found")
    content = (request.content or row["content"]).strip()
    vector = await embedding(content, get_settings()["embed_model"]) if request.content else None
    with db() as connection:
        connection.execute(
            "UPDATE memories SET content = ?, kind = ?, embedding = COALESCE(?, embedding), updated_at = ? WHERE id = ?",
            (content, request.kind or row["kind"], json.dumps(vector) if vector else None, timestamp(), memory_id),
        )
        row = connection.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
    return knowledge.memory_view(row)


@app.delete("/api/memories/{memory_id}")
def delete_memory(memory_id: str) -> dict[str, bool]:
    with db() as connection:
        deleted = connection.execute("DELETE FROM memories WHERE id = ?", (memory_id,)).rowcount
    if not deleted:
        raise HTTPException(404, "Memory not found")
    activity("memory", "Memory deleted", memory_id)
    return {"deleted": True}


# ============================================================
# STARTUP
# ============================================================

initialize()

