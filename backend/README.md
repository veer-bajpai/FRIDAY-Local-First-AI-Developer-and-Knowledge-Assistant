# FRIDAY Backend

The local FastAPI service powers FRIDAY Browser and the coding agent. It provides document ingestion, hybrid search, long-term memory, collections, settings, health checks, and agent sessions. See [the architecture guide](../docs/architecture.md) for component boundaries and data flows, and [operations](../docs/operations.md) for security and configuration.

## Run locally

From the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
$env:FRIDAY_DATA_DIR = "$PWD\data"
Push-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
Pop-Location
```

The API is available at `http://localhost:8000`; interactive API documentation is at `/docs`. On macOS/Linux, create the environment with `python3 -m venv .venv` and activate it with `source .venv/bin/activate`.

The Browser development server runs separately with `npm run dev`. Set `NEXT_PUBLIC_API_URL=http://localhost:8000` in `.env.local`.

## Data and models

SQLite and uploaded source files are stored under `$FRIDAY_DATA_DIR`; the CLI defaults to `~/.friday/data`. The database stores documents, chunks, activity, collections, settings, and memories. The coding agent stores its resumable sessions under the same data directory.

Ollama is optional for health, uploads, and lexical search. It is used by the coding agent and can provide document embeddings and image understanding. Configure `OLLAMA_URL`, `FRIDAY_MODEL`, `FRIDAY_EMBED_MODEL`, and optionally `FRIDAY_VISION_MODEL` in the launching environment.

## API areas

- `/api/documents`: document listing, upload, processing status, reprocessing, and deletion
- `/api/search`: hybrid search with optional collection or document filters
- `/api/collections`: collection management and document associations
- `/api/memories`: long-term memory management and relevance search
- `/api/settings`: embedding and retrieval settings
- `/api/activity`: recent workspace events
- `/api/agent/*`: coding-agent sessions, turns, approvals, history, and health

The standalone chat, conversation, model-list, and legacy status endpoints have been removed. Agent model calls remain part of the coding-agent runtime.
