import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


DATA_DIR = Path(os.getenv("FRIDAY_DATA_DIR", "./data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "friday.db"

OLLAMA_URL = os.getenv(
    "OLLAMA_URL",
    "http://localhost:11434"
).rstrip("/")

LEGACY_SYSTEM_PROMPT = """You are FRIDAY, a local-first developer assistant.
Use concise Markdown. Put every code sample in a fenced Markdown code block with a language tag.
Prefer headings and lists when useful. Never put code in an unfenced paragraph."""
PREVIOUS_SYSTEM_PROMPT = "You are FRIDAY. Answer concisely in Markdown. Fence code samples with a language tag."
SYSTEM_PROMPT = "You are FRIDAY. Answer directly and concisely. Do not include code, code blocks, or programming examples unless the user explicitly asks for code or asks a programming-related question. When code is relevant, use concise Markdown and fenced code blocks with a language tag."


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="Friday API",
    version="1.0.0"
)


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


app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# REQUEST MODELS
# ============================================================

class ChatRequest(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=4000
    )
    k: int | None = Field(
        default=None,
        ge=1,
        le=20
    )
    conversation_id: str | None = None
    collection_id: str | None = None


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


class TitleRequest(BaseModel):
    title: str = Field(
        min_length=1,
        max_length=120
    )


class SettingsUpdate(BaseModel):
    model: str | None = None
    embed_model: str | None = None
    top_k: int | None = Field(
        default=None,
        ge=1,
        le=20
    )
    max_distance: float | None = Field(
        default=None,
        ge=0,
        le=2
    )
    temperature: float | None = Field(
        default=None,
        ge=0,
        le=2
    )
    system_prompt: str | None = None


# ============================================================
# HELPERS
# ============================================================

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def db() -> sqlite3.Connection:
    DATA_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    connection = sqlite3.connect(DB_PATH)

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

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
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT NOT NULL
                    REFERENCES conversations(id)
                    ON DELETE CASCADE,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

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

        defaults = {
            "model": os.getenv(
                "FRIDAY_MODEL",
                "llama3.2:latest"
            ),
            "embed_model": os.getenv(
                "FRIDAY_EMBED_MODEL",
                "nomic-embed-text:latest"
            ),
            "top_k": "3",
            "max_distance": "2",
            "temperature": "0.2",
            "system_prompt": SYSTEM_PROMPT,
        }

        connection.executemany(
            """
            INSERT OR IGNORE INTO settings(key, value)
            VALUES (?, ?)
            """,
            defaults.items()
        )
        connection.execute(
            """
            UPDATE settings
            SET value = ?
            WHERE key = 'system_prompt' AND value IN (?, ?)
            """,
            (SYSTEM_PROMPT, LEGACY_SYSTEM_PROMPT, PREVIOUS_SYSTEM_PROMPT)
        )


@app.on_event("startup")
def startup() -> None:
    initialize()


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

    values["top_k"] = int(
        values["top_k"]
    )

    values["max_distance"] = float(
        values["max_distance"]
    )

    values["temperature"] = float(
        values["temperature"]
    )

    return values


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
    words = text.split()

    return [
        " ".join(
            words[index:index + 250]
        )
        for index in range(
            0,
            len(words),
            220
        )
    ] or [""]


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
        async with httpx.AsyncClient(
            timeout=60
        ) as client:

            response = await client.post(
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

async def ingest(
    document_id: str
) -> None:

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

        path = Path(
            document["file_path"]
        )

        if document["file_type"] in {
            "png",
            "jpg",
            "jpeg",
            "gif",
            "webp",
            "bmp",
        }:
            text = ""

        elif document["file_type"] == "pdf":

            from pypdf import PdfReader

            text = "\n".join(
                page.extract_text() or ""
                for page in PdfReader(path).pages
            )

        else:
            text = path.read_text(
                encoding="utf-8",
                errors="ignore"
            )

        with db() as connection:

            connection.execute(
                """
                DELETE FROM chunks
                WHERE document_id = ?
                """,
                (document_id,)
            )

            for index, content in enumerate(
                split_chunks(text)
            ):

                vector = await embedding(
                    content,
                    get_settings()["embed_model"]
                )

                connection.execute(
                    """
                    INSERT INTO chunks(
                        document_id,
                        chunk_index,
                        content,
                        embedding
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        document_id,
                        index,
                        content,
                        json.dumps(vector)
                        if vector
                        else None
                    )
                )

            connection.execute(
                """
                UPDATE documents
                SET
                    status = 'ready',
                    error = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    timestamp(),
                    document_id
                )
            )

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

def retrieve(
    query: str,
    limit: int,
    collection_id: str | None = None,
    max_distance: float | None = None,
) -> list[dict[str, Any]]:

    sql = """
        SELECT
            chunks.*,
            documents.name
        FROM chunks
        JOIN documents
            ON documents.id = chunks.document_id
        WHERE documents.status = 'ready'
    """

    params: list[Any] = []

    if collection_id:

        sql += """
            AND chunks.document_id IN (
                SELECT document_id
                FROM collection_documents
                WHERE collection_id = ?
            )
        """

        params.append(
            collection_id
        )

    with db() as connection:
        rows = connection.execute(
            sql,
            params
        ).fetchall()

    ranked = sorted(
        (
            (
                lexical_score(
                    query,
                    row["content"]
                ),
                row
            )
            for row in rows
        ),
        key=lambda item: item[0],
        reverse=True
    )

    filtered: list[dict[str, Any]] = []
    for score, row in ranked:
        if score <= 0:
            continue
        distance = 1 - score
        if max_distance is not None and distance > max_distance:
            continue
        filtered.append(
            {
                "document_id": row["document_id"],
                "title": row["name"],
                "chunk": row["chunk_index"],
                "distance": round(distance, 4),
                "snippet": row["content"][:240],
            }
        )
        if len(filtered) >= limit:
            break

    return filtered


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health() -> dict[str, str]:

    return {
        "status": "ok",
        "service": "friday-api"
    }


@app.get("/")
def root() -> dict[str, str]:

    return {
        "service": "friday-api",
        "status": "ok",
        "docs": "/docs"
    }


# ============================================================
# STATUS
# ============================================================

@app.get("/api/status")
async def status() -> dict[str, Any]:

    ollama = "offline"

    try:

        async with httpx.AsyncClient(
            timeout=5
        ) as client:

            response = await client.get(
                f"{OLLAMA_URL}/api/tags"
            )

            response.raise_for_status()

            ollama = "online"

    except Exception:
        pass

    with db() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM documents"
        ).fetchone()[0]

    current = get_settings()

    return {
        "ollama": ollama,
        "embedding_model": current["embed_model"],
        "generation_model": current["model"],
        "documents": count,
    }


# ============================================================
# MODELS
# ============================================================

@app.get("/api/models")
async def models() -> dict[str, Any]:

    try:

        async with httpx.AsyncClient(
            timeout=10
        ) as client:

            response = await client.get(
                f"{OLLAMA_URL}/api/tags"
            )

            response.raise_for_status()

            return response.json()

    except Exception:

        return {
            "models": []
        }


# ============================================================
# SETTINGS API
# ============================================================

@app.get("/api/settings")
def settings() -> dict[str, Any]:

    return get_settings()


@app.put("/api/settings")
def update_settings(
    request: SettingsUpdate
) -> dict[str, Any]:

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

    content = await file.read()

    name = Path(
        file.filename or "untitled.txt"
    ).name

    document_id = str(
        uuid.uuid4()
    )

    path = (
        UPLOAD_DIR /
        f"{document_id}-{name}"
    )

    path.write_bytes(content)

    created = timestamp()

    with db() as connection:

        connection.execute(
            """
            INSERT INTO documents
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
                path.suffix.lower().lstrip(".")
                or "txt",
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

        connection.execute(
            """
            DELETE FROM documents
            WHERE id = ?
            """,
            (document_id,)
        )

    Path(
        row["file_path"]
    ).unlink(
        missing_ok=True
    )

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
def search(
    request: SearchRequest
) -> dict[str, Any]:

    started = time.perf_counter()

    current = get_settings()

    return {
        "query": request.query,
        "results": retrieve(
            request.query,
            request.k,
            request.collection_id,
            current.get("max_distance"),
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
# CONVERSATIONS
# ============================================================

@app.post("/api/conversations")
def create_conversation(
    request: TitleRequest = TitleRequest(
        title="New conversation"
    )
) -> dict[str, Any]:

    conversation = {
        "id": str(uuid.uuid4()),
        "title": request.title,
        "created_at": timestamp(),
        "updated_at": timestamp(),
    }

    with db() as connection:

        connection.execute(
            """
            INSERT INTO conversations
            VALUES (?, ?, ?, ?)
            """,
            tuple(
                conversation.values()
            )
        )

    return {
        **conversation,
        "messages": []
    }


@app.get("/api/conversations")
def list_conversations() -> list[dict[str, Any]]:

    with db() as connection:

        rows = connection.execute(
            """
            SELECT *
            FROM conversations
            ORDER BY updated_at DESC
            """
        ).fetchall()

    return [
        dict(row)
        for row in rows
    ]


@app.get(
    "/api/conversations/{conversation_id}"
)
def get_conversation(
    conversation_id: str
) -> dict[str, Any]:

    with db() as connection:

        conversation = connection.execute(
            """
            SELECT id, title, created_at, updated_at
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        ).fetchone()

        messages = connection.execute(
            """
            SELECT role, content, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id
            """,
            (conversation_id,)
        ).fetchall()

    if not conversation:
        raise HTTPException(
            404,
            "Conversation not found"
        )

    return {
        **dict(conversation),
        "messages": [
            dict(message)
            for message in messages
        ],
    }


@app.patch(
    "/api/conversations/{conversation_id}"
)
def rename_conversation(
    conversation_id: str,
    request: TitleRequest
) -> dict[str, str]:

    with db() as connection:

        cursor = connection.execute(
            """
            UPDATE conversations
            SET
                title = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                request.title,
                timestamp(),
                conversation_id
            )
        )

    if cursor.rowcount == 0:

        raise HTTPException(
            404,
            "Conversation not found"
        )

    return {
        "id": conversation_id,
        "title": request.title
    }


@app.delete(
    "/api/conversations/{conversation_id}"
)
def delete_conversation(
    conversation_id: str
) -> dict[str, bool]:

    with db() as connection:

        cursor = connection.execute(
            """
            DELETE FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        )

    if cursor.rowcount == 0:

        raise HTTPException(
            404,
            "Conversation not found"
        )

    return {
        "deleted": True
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
            INSERT INTO collections
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

def recent_history(rows: list[sqlite3.Row]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    remaining_chars = 6000

    for row in rows:
        if len(selected) >= 8 or remaining_chars <= 0:
            break

        content = row["content"][-remaining_chars:]
        selected.append({"role": row["role"], "content": content})
        remaining_chars -= len(content)

    return list(reversed(selected))

@app.post("/api/chat")
async def chat(
    request: ChatRequest
) -> dict[str, Any]:

    started = time.perf_counter()

    current = get_settings()

    conversation_id = request.conversation_id

    if not conversation_id:
        conversation_id = create_conversation().get(
            "id"
        )

    with db() as connection:

        conversation = connection.execute(
            """
            SELECT id
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        ).fetchone()

        history = connection.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (conversation_id,)
        ).fetchall()

    if not conversation:

        raise HTTPException(
            404,
            "Conversation not found"
        )

    sources = retrieve(
        request.question,
        request.k or current["top_k"],
        request.collection_id,
        current.get("max_distance"),
    )

    context = "\n\n".join(
        f"[{source['title']} / chunk {source['chunk']}]\n"
        f"{source['snippet']}"
        for source in sources
    )

    if not context:

        prompt = request.question

    else:

        prompt = (
            "Use these local sources when relevant:\n"
            f"{context}\n\n"
            f"Question: {request.question}"
        )

    messages = [
        {
            "role": "system",
            "content": current["system_prompt"]
        },
        *recent_history(history),
        {
            "role": "user",
            "content": prompt
        },
    ]

    answer = (
        "FRIDAY cannot connect to Ollama. "
        "Start Ollama or configure OLLAMA_URL."
    )

    try:

        async with httpx.AsyncClient(
            timeout=120
        ) as client:

            response = await client.post(
                f"{OLLAMA_URL}/api/chat",
                json={
                    "model": current["model"],
                    "messages": messages,
                    "stream": False,
                    "keep_alive": "5m",
                    "options": {
                        "temperature": current["temperature"],
                        "num_predict": 256,
                    },
                }
            )

            response.raise_for_status()

            answer = (
                response.json()
                .get("message", {})
                .get("content", "")
                .strip()
                or "Ollama returned an empty response."
            )

    except Exception as exc:

        activity(
            "chat",
            "Chat failed",
            str(exc),
            "error"
        )

    created = timestamp()

    with db() as connection:

        connection.execute(
            """
            INSERT INTO messages(
                conversation_id,
                role,
                content,
                created_at
            )
            VALUES (
                ?,
                'user',
                ?,
                ?
            )
            """,
            (
                conversation_id,
                request.question,
                created
            )
        )

        connection.execute(
            """
            INSERT INTO messages(
                conversation_id,
                role,
                content,
                created_at
            )
            VALUES (
                ?,
                'assistant',
                ?,
                ?
            )
            """,
            (
                conversation_id,
                answer,
                created
            )
        )

        connection.execute(
            """
            UPDATE conversations
            SET
                title = CASE
                    WHEN title = 'New conversation'
                    THEN ?
                    ELSE title
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                request.question[:60],
                created,
                conversation_id
            )
        )

    activity(
        "chat",
        "Conversation updated",
        request.question[:80],
        duration_ms=(
            time.perf_counter()
            - started
        ) * 1000
    )

    return {
        "answer": answer,
        "sources": sources,
        "conversation_id": conversation_id,
        "created_at": created,
    }


# ============================================================
# STREAMING CHAT
# ============================================================

@app.post(
    "/api/chat/stream"
)
async def chat_stream(
    request: ChatRequest
) -> StreamingResponse:

    current = get_settings()

    conversation_id = (
        request.conversation_id
        or create_conversation().get("id")
    )

    with db() as connection:

        conversation = connection.execute(
            """
            SELECT id
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,)
        ).fetchone()

        history = connection.execute(
            """
            SELECT role, content
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id DESC
            LIMIT 8
            """,
            (conversation_id,)
        ).fetchall()

    if not conversation:

        raise HTTPException(
            404,
            "Conversation not found"
        )

    sources = retrieve(
        request.question,
        request.k or current["top_k"],
        request.collection_id,
        current.get("max_distance"),
    )

    context = "\n\n".join(
        f"[{source['title']} / chunk {source['chunk']}]\n"
        f"{source['snippet']}"
        for source in sources
    )

    if not context:

        prompt = request.question

    else:

        prompt = (
            "Use these local sources when relevant:\n"
            f"{context}\n\n"
            f"Question: {request.question}"
        )

    messages = [
        {
            "role": "system",
            "content": current["system_prompt"]
        },
        *recent_history(history),
        {
            "role": "user",
            "content": prompt
        },
    ]

    async def events():

        answer_parts: list[str] = []

        try:

            async with httpx.AsyncClient(
                timeout=120
            ) as client:

                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/chat",
                    json={
                        "model": current["model"],
                        "messages": messages,
                        "stream": True,
                        "keep_alive": "5m",
                        "options": {
                            "temperature": current["temperature"],
                            "num_predict": 256,
                        },
                    }
                ) as response:

                    response.raise_for_status()

                    async for line in response.aiter_lines():

                        if not line:
                            continue

                        data = json.loads(line)

                        token = (
                            data
                            .get("message", {})
                            .get("content", "")
                        )

                        if token:

                            answer_parts.append(
                                token
                            )

                            yield (
                                "data: "
                                + json.dumps({
                                    "token": token
                                })
                                + "\n\n"
                            )

            answer = (
                "".join(answer_parts).strip()
                or "Ollama returned an empty response."
            )

            created = timestamp()

            with db() as connection:

                connection.execute(
                    """
                    INSERT INTO messages(
                        conversation_id,
                        role,
                        content,
                        created_at
                    )
                    VALUES (
                        ?,
                        'user',
                        ?,
                        ?
                    )
                    """,
                    (
                        conversation_id,
                        request.question,
                        created
                    )
                )

                connection.execute(
                    """
                    INSERT INTO messages(
                        conversation_id,
                        role,
                        content,
                        created_at
                    )
                    VALUES (
                        ?,
                        'assistant',
                        ?,
                        ?
                    )
                    """,
                    (
                        conversation_id,
                        answer,
                        created
                    )
                )

                connection.execute(
                    """
                    UPDATE conversations
                    SET
                        title = CASE
                            WHEN title = 'New conversation'
                            THEN ?
                            ELSE title
                        END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        request.question[:60],
                        created,
                        conversation_id
                    )
                )

            yield (
                "data: "
                + json.dumps({
                    "done": True,
                    "conversation_id": conversation_id,
                    "sources": sources
                })
                + "\n\n"
            )

        except Exception as exc:

            activity(
                "chat",
                "Streaming chat failed",
                str(exc),
                "error"
            )

            yield (
                "data: "
                + json.dumps({
                    "error": "FRIDAY could not reach Ollama."
                })
                + "\n\n"
            )

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
    )


# ============================================================
# STARTUP
# ============================================================

initialize()