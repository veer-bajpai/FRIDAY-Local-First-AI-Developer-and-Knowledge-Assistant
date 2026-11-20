# FRIDAY operations guide

Security, configuration, APIs and recovery behaviour of the FastAPI backend.

## Security

Every request passes `backend/app/security.py`:

| Control | Behaviour |
| --- | --- |
| Authentication | `FRIDAY_AUTH=auto` (default): requests from this machine (loopback) or `FRIDAY_TRUSTED_NETWORKS` need no token; any other client must send the API token. `FRIDAY_AUTH=required`: every request except `/health` and `/api/health` needs the token. |
| API token | `FRIDAY_API_TOKEN`, or generated once into `$FRIDAY_DATA_DIR/api_token` (owner-only permissions). Send `Authorization: Bearer <token>` or `X-Friday-Token: <token>`. |
| Trusted hosts | The `Host` header must be `localhost`, `127.0.0.1`, `::1` or listed in `FRIDAY_ALLOWED_HOSTS` (blocks DNS-rebinding attacks from web pages). |
| Rate limits | Per client per minute: chat 30, uploads 20, agent turns 30, everything else 600 (`FRIDAY_RATE_CHAT`, `FRIDAY_RATE_UPLOAD`, `FRIDAY_RATE_AGENT`, `FRIDAY_RATE_DEFAULT`). |
| Size limits | JSON bodies 1 MB; uploads `FRIDAY_MAX_UPLOAD_BYTES` (25 MB). Unsupported file types are rejected with HTTP 415. |
| Agent routes | Additionally require a local client or a valid token; the project root may not be a drive root or your home folder. |
| Bypass mode | Refused unless `FRIDAY_ALLOW_BYPASS=1`. File changes, commands, git mutations and network access keep their approval prompts. |
| Shell | "Safe" read-only commands run without approval only when every path argument stays inside the workspace (`cat ~/.ssh/...`, `..`, `C:\...`, `%USERPROFILE%`, UNC paths all require approval). |
| Docker | Ports are published on `127.0.0.1` only; the bridge network is trusted explicitly. |

The command denylist is not a sandbox: run untrusted repositories in Docker.

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `FRIDAY_DATA_DIR` | `./data` (CLI: `~/.friday/data`) | SQLite database, uploads, agent sessions, API token |
| `OLLAMA_URL` / `OLLAMA_HOST` | `http://localhost:11434` | Ollama server |
| `FRIDAY_MODEL` | `llama3.2:latest` | Default chat / agent model |
| `FRIDAY_EMBED_MODEL` | `nomic-embed-text:latest` | Embedding model |
| `FRIDAY_FALLBACK_MODELS` | `qwen2.5-coder:7b,llama3.2:latest,llama3.1:8b,qwen3:8b` | Tried in order when the configured model is not installed |
| `FRIDAY_VISION_MODEL` | *(unset)* | Ollama vision model for images, e.g. `llava:7b`, `qwen2.5vl:7b` |
| `FRIDAY_TESSERACT_CMD` | *(auto)* | Full path to `tesseract.exe`. Normally not needed: FRIDAY checks PATH, then `C:\Program Files\Tesseract-OCR` |
| `FRIDAY_MAX_TOKENS` | `2048` | Chat reply length limit (`-1` = model default) |
| `FRIDAY_OLLAMA_TIMEOUT` | `300` | Seconds to wait between streamed chunks (model loading) |
| `FRIDAY_NUM_CTX` | `8192` | Context window passed to Ollama |
| `FRIDAY_TOOL_TIMEOUT` | `300` | Seconds before an agent tool call is stopped |
| `FRIDAY_MAX_STEPS` | `40` | Agent tool-loop steps per turn |
| `FRIDAY_REPO_MAP_FILES` | `120` | Files listed in the agent's repository overview |
| `FRIDAY_OCR_LANG` | `eng` | Tesseract languages (e.g. `eng+hin`) |
| `FRIDAY_OCR_MAX_PAGES` | `50` | Scanned PDF pages OCR'd per document |
| `FRIDAY_AUTH`, `FRIDAY_API_TOKEN`, `FRIDAY_ALLOWED_HOSTS`, `FRIDAY_TRUSTED_NETWORKS`, `FRIDAY_ALLOW_BYPASS` | see Security | |
| `FRIDAY_LOG_LEVEL` / `FRIDAY_LOG_FORMAT` | `INFO` / `text` | Logging (`json` for structured logs) |

Optional installs: `pip install -r backend/requirements-ocr.txt` plus the Tesseract program
(Windows: `winget install UB-Mannheim.TesseractOCR`) for OCR; `ollama pull llava:7b` for image understanding.

## New and changed API routes

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Readiness: database + FTS index, Ollama + model availability, agent store, MCP, OCR/vision. `status` is `ok`, `degraded` or `error`. `/health` stays a liveness probe. |
| GET/POST | `/api/memories` | List (`?q=` for relevance search) / create long-term memories |
| PATCH/DELETE | `/api/memories/{id}` | Edit / delete a memory |
| POST | `/api/documents/{id}/reprocess` | Re-extract and re-index a document |
| POST | `/api/search` | Adds `document_ids` filter; results include `page`, `method`, `chunk_id` |
| POST | `/api/chat/stream` | Events: `{meta}` (model used, fallback note), `{token}` per model token, then `{done, sources, memories}` or `{error}` |
| POST | `/api/agent/sessions` | Adds `resume_id`; the response includes `status`, `resumable`, `model_note` |
| GET | `/api/agent/sessions/{id}/history` | Conversation and tool log of a session |

Typing `remember: <fact>` (or `/remember <fact>`) in chat stores a memory without calling the model.

## Agent sessions and recovery

* Each session is saved to `$FRIDAY_DATA_DIR/agent_sessions/<id>.json` after every tool call, approval and turn. The file holds the transcript, todos, undo checkpoints, tool log, usage and status (`running`, `awaiting_approval`, `completed`, `failed`, `cancelled`, `interrupted`).
* On restart, sessions are restored. A session that was running or waiting for approval becomes `interrupted`: tool calls without results are recorded as *Not executed* (nothing is replayed), pending approvals are dropped, and the model is told to re-check the files before continuing.
* `/rewind` and `/undo` work after a restart. The old metadata-only `agent_sessions.json` is migrated once and renamed `.migrated`.
* The terminal console closes (deletes) its session on quit, as before. The browser keeps its session across tab switches and reloads, and "New session" closes it.

## Logs and health

Every response carries `X-Request-ID`, which matches the access-log line. Unhandled errors return a generic 500 with that id; details stay in the log. Document ingestion interrupted by a shutdown is re-queued at startup. A broken MCP integration is reported in `/api/health` and does not stop the backend.

