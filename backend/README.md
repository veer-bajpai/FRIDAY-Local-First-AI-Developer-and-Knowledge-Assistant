# FRIDAY API and Backend Guide

The backend is the local FastAPI service for document ingestion, hybrid search, long-term memory, conversations, collections, settings, and Ollama-backed chat. The web client and terminal client both call this service and share its SQLite database. See [the architecture guide](../docs/architecture.md) for the complete runtime, startup sequence, API routes, data flows, schema, and current limitations.

## How the application works

```text
Browser or `friday chat`
	|
	| REST + server-sent events
	v
FastAPI on 127.0.0.1:8000
	|
	+--> SQLite database and uploaded files
	+--> hybrid chunk retrieval (FTS5 + vector index)
	+--> Ollama on localhost:11434
		    |
		    +--> llama3.2 for chat
		    +--> nomic-embed-text for stored ingestion embeddings
```

Chat retrieves relevant text chunks first, adds them to the prompt with recent conversation history, and streams the Ollama response back to the client. Search is hybrid retrieval (SQLite FTS5 BM25 + in-memory embedding vector index, reciprocal-rank fusion); every result is attributed to its document, chunk and PDF page.

## Install from GitHub

The supported end-user path is the repository installer described in the root [README](../README.md). On Windows, after the installer files have been pushed to `main`, run:

```powershell
irm https://raw.githubusercontent.com/veer-bajpai/FRIDAY-Local-First-AI-Knowledge-Assistant/main/install.ps1 | iex
```

Then open a new terminal and run `friday browser` or `friday chat`. The installer provisions the CLI environment under `~/.friday`, not inside the repository's development `.venv`.

## Requirements

- Python 3.11+
- Dependencies from `backend/requirements.txt`
- Optional Ollama at `OLLAMA_URL` (defaults to `http://localhost:11434`) for embeddings and chat generation

Run from the repository root on Windows:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
$env:FRIDAY_DATA_DIR = "$PWD\data"
Push-Location backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
Pop-Location
```

The API is available at `http://localhost:8000`. Interactive documentation is at `http://localhost:8000/docs`, and the health check is `http://localhost:8000/health`.

Verify the service before starting the frontend:

```powershell
Invoke-WebRequest http://localhost:8000/health | Select-Object -ExpandProperty Content
```

Expected response:

```json
{ "status": "ok" }
```

On macOS/Linux, create and activate `.venv` with `python3 -m venv .venv` and `source .venv/bin/activate`, install `pip install -r backend/requirements.txt`, set `FRIDAY_DATA_DIR="$PWD/data"`, then run `python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload` from the `backend/` directory.

## Data and retrieval

SQLite is created at `$FRIDAY_DATA_DIR/friday.db`, and uploaded source files are stored in `$FRIDAY_DATA_DIR/uploads/`. If `FRIDAY_DATA_DIR` is unset, the backend uses `./data` relative to its working directory. The Friday CLI sets a shared default of `~/.friday/data` for both web and terminal chat.

Extraction (`app/extraction.py`) handles PDF (per page, OCR for scanned pages), DOCX, PPTX, XLSX, CSV/TSV, JSON, HTML, Markdown/text and code. Images are described by a local Ollama vision model (`FRIDAY_VISION_MODEL`) and/or OCR'd with Tesseract; when neither is available the document fails with an explanation (image metadata is never stored as content). Ollama embeddings are stored per chunk and served from an in-memory vector index; chunks also go into an FTS5 index. See [operations](../docs/operations.md).

The API can serve health, settings, documents, collections, history, and keyword (FTS5) search without Ollama. Chat generation requires an available generation model at `OLLAMA_URL`.

## Environment variables

| Variable                 | Default                                        | Purpose                                                    |
| ------------------------ | ---------------------------------------------- | ---------------------------------------------------------- |
| `FRIDAY_DATA_DIR`        | `./data` relative to backend working directory | SQLite database and uploads root                           |
| `OLLAMA_URL`             | `http://localhost:11434`                       | Ollama API base URL                                        |
| `FRIDAY_MODEL`           | `llama3.2:latest`                              | Default chat model when settings are first created         |
| `FRIDAY_EMBED_MODEL`     | `nomic-embed-text:latest`                      | Default embedding model when settings are first created    |
| `FRIDAY_MAX_TOKENS`      | `2048`                                         | Maximum generated chat tokens (`-1` = model default) |
| `FRIDAY_ALLOWED_ORIGINS` | Built-in local and deployed frontend origins   | Additional comma-separated CORS origins                    |

The backend does not load `.env` files itself. The launcher and Docker Compose provide their configured environment variables.

The backend works without Ollama for health, browsing, uploads, and keyword retrieval. Chat generation requires an available model at `OLLAMA_URL`. Requests from this machine are allowed; any other client needs the API token (see [operations](../docs/operations.md#security)). Full variable list: [docs/operations.md](../docs/operations.md).
