# FRIDAY API and Backend Guide

The backend is the local FastAPI service for document ingestion, lexical search, conversations, collections, settings, and Ollama-backed chat. The web client and terminal client both call this service and share its SQLite database. See [the architecture guide](../docs/architecture.md) for the complete runtime, startup sequence, API routes, data flows, schema, and current limitations.

## How the application works

```text
Browser or `friday chat`
	|
	| REST + server-sent events
	v
FastAPI on 127.0.0.1:8000
	|
	+--> SQLite database and uploaded files
	+--> lexical chunk retrieval
	+--> Ollama on localhost:11434
		    |
		    +--> llama3.2 for chat
		    +--> nomic-embed-text for stored ingestion embeddings
```

Chat retrieves relevant text chunks first, adds them to the prompt with recent conversation history, and streams the Ollama response back to the client. Search is currently lexical token-overlap ranking; stored embeddings are not used by a vector index yet.

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

PDF text is extracted with `pypdf`; other non-image files are read as UTF-8 text. Raster images currently have no OCR/image extraction. Ollama embeddings are requested per chunk and stored when available, but current search and chat retrieval rank text chunks lexically; embeddings are not used by a vector index.

The API can serve health, settings, documents, collections, history, and lexical search without Ollama. Chat generation requires an available generation model at `OLLAMA_URL`.

## Environment variables

| Variable                 | Default                                        | Purpose                                                    |
| ------------------------ | ---------------------------------------------- | ---------------------------------------------------------- |
| `FRIDAY_DATA_DIR`        | `./data` relative to backend working directory | SQLite database and uploads root                           |
| `OLLAMA_URL`             | `http://localhost:11434`                       | Ollama API base URL                                        |
| `FRIDAY_MODEL`           | `llama3.2:latest`                              | Default chat model when settings are first created         |
| `FRIDAY_EMBED_MODEL`     | `nomic-embed-text:latest`                      | Default embedding model when settings are first created    |
| `FRIDAY_MAX_TOKENS`      | `128`                                          | Maximum generated chat tokens; increase for longer answers |
| `FRIDAY_ALLOWED_ORIGINS` | Built-in local and deployed frontend origins   | Additional comma-separated CORS origins                    |

The backend does not load `.env` files itself. The launcher and Docker Compose provide their configured environment variables.

The backend works without Ollama for health, browsing, uploads, and lexical retrieval. Chat generation requires an available model at `OLLAMA_URL`. The API currently has no authentication; do not expose it to untrusted networks.
