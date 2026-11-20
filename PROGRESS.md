# FRIDAY Progress

## FRIDAY v10.00 (20 Nov 2026)

Consolidated release built from the working copy, `FRIDAY_v4.00` and the `FRIDAY_Browser_UI_update` package.

- Version set to 10.0.0 (`package.json`, `friday_agent.__version__`, FastAPI app version).
- Keeps the newest code in every file: the Friday Code terminal (`terminal.py` with `scene.py` and the Patch mascot), prompt-based tool routing (`routing.py`), diagnostics (`diagnostics.py`), model warm-up, the `/api/agent/sessions/{id}/cancel` endpoint, and persisted `tool_routing` per session.
- Browser UI (Bento dashboard, light/dark themes, `src/app/ui/`) unchanged; the update package's `src/` was identical to it.
- From v4.00: removed the stale `src/app/workspace.css`, `src/app/thinking/ThinkingExample.tsx` and its now-unused `src/app/thinking/useThinkingTimer.ts`; dropped the runtime file `data/agent_sessions.json` (created on first run).
- Not taken from the update package: its older "operator console" terminal and engine without routing, warm-up or cancel, which would have removed those features.

## Audit and hardening pass (Nov 2026)

The terminal console (`friday_agent/terminal.py`) was kept byte-for-byte unchanged.

| Step | Status | Result |
| --- | --- | --- |
| 1 Inspect | done | Two agent loops mapped (`friday_agent/engine.py` = maintained, used by terminal and browser; `backend/app/agent/loop.py` = legacy `/api/agent/stream`, no bundled client) |
| 2 Security | done | `backend/app/security.py`: token auth (auto/required), trusted hosts, rate and size limits, security headers; bypass mode gated; safe shell commands confined to the workspace; Docker ports on 127.0.0.1 |
| 3 Session persistence | done | `friday_agent/session_store.py`: full transcript, todos, undo checkpoints, tool log and status survive restarts; interrupted calls marked "Not executed", never replayed; resume and history APIs |
| 4 Knowledge and memory | done | `backend/app/knowledge.py`: FTS5 BM25 + in-memory vector index, reciprocal-rank fusion, hash dedup, page attribution, document and collection filters; `memories` table, `/api/memories`, memories injected into chat; in-place DB migration |
| 5 Documents, OCR, images | done | `backend/app/extraction.py`: PDF per page with OCR for scanned pages, DOCX/PPTX/XLSX, CSV/JSON/HTML, code; images via Ollama vision model and/or Tesseract; clear failures; 415 for unsupported types |
| 6 Coding agent | done | UTF-8 and CRLF-preserving edits, undo and rewind; glob confined to the workspace; per-tool timeouts; repeated-failure and repeated-denial guards; context trimming; shared git-aware repo map |
| 7 Streaming and models | done | Real per-token SSE confirmed; one Ollama client; in-stream error detection; model fallback with note; 2048-token default (was 128); errors no longer saved as answers; partial replies kept |
| 8 Reliability | done | Structured/JSON logging with request ids; `/api/health` readiness; SQLite connections now closed, WAL mode, busy timeout; interrupted ingestion re-queued; reprocess endpoint; MCP failures isolated; startup ran twice (fixed) |
| 9 Frontend | done | Agent todos now update live (event-name bug); agent session survives tab switches and reloads; New session; chat shows sources, model notes and real error messages; upload types and failure reasons |
| 10 Tests | done | See below |
| 11 Docs | done | README, backend README, architecture, new `docs/operations.md` |

## Browser UI redesign (Bento)

- New design system (`src/app/globals.css` tokens, `src/app/ui/ui.css`) with light and dark themes. Theme follows the OS until changed with the header toggle or Settings, is remembered in `localStorage`, and is applied before first paint (no flash).
- New monochrome "Spark" logo (`src/app/ui/Logo.tsx`, `public/favicon.svg`).
- Dashboard home page with real data: conversations, documents, memories, agent sessions, a 7/14/30-day activity chart, agent runs, a knowledge-base breakdown, and system health from `/api/health`.
- Chat: rotating working words, live timer, indeterminate progress and real retrieval/generation steps, which collapse to "Answered in Ns · sources · memories". Source chips, code blocks with language and copy, copy/ask-again, and a context rail (sources, memories, run).
- New Memory page; Documents gain reprocess/delete; History gains delete; Search shows page and score; Settings gains appearance. Responsive layout with an off-canvas menu. Ctrl+K search, Ctrl+Shift+O new chat.
- Components split out of `page.tsx` into `src/app/ui/` (api, theme, charts, WorkingStatus, ChatView, Dashboard, Views). Removed `src/app/workspace.css` and `src/app/thinking/ThinkingExample.tsx`.
- Verified: `tsc --noEmit`, `eslint` (0 problems), `next build`, plus screenshots of every page in light, dark and phone widths against a live backend.

## Bugs found and fixed

- `/api/chat` replies were cut at 128 tokens by default.
- Error text ("cannot connect to Ollama") was saved as the assistant's answer in history.
- Inserts into `documents`, `conversations` and `collections` used column position and broke when a column was added.
- `with db()` never closed SQLite connections.
- On Windows the file tools read with cp1252 but wrote UTF-8 (garbled non-ASCII), and every edit converted CRLF to LF.
- `glob` patterns with `..` or absolute paths reached outside the workspace; `cat ~/...` and `cat C:\...` ran without approval.
- FastAPI 0.115 ran the agent router's startup twice, spawning MCP servers twice.
- A configured but uninstallable MCP server stopped the backend from starting.
- The browser agent panel read live todos from the wrong event field, deleted its session when you switched tabs, and allowed 8000-character tasks that the API rejects (limit 4000).
- Image uploads became empty "ready" documents.

## Verification (actually run)

- `pytest -q` (repo root): **78 passed** (was 32; none removed or weakened)
- `npx tsc --noEmit`: passed
- `npx eslint src`: 0 problems (run with the standard `eslint-config-next` flat config, because `eslint.config.mjs` was not in the shared source)
- `npx next build`: passed
- Live end-to-end run (uvicorn + fake Ollama HTTP server): host check, bypass refusal, upload, hybrid search, 415, token streaming with sources, `remember:`, agent edit with approval keeping CRLF, backend restart, session resume with history and tool log, `/rewind` after restart, unchanged terminal console connecting and running a turn
- Real Tesseract OCR on a generated image and a scanned PDF

## Known limitations

- The terminal console is unchanged: it starts a new session on launch and deletes it on quit, so it does not resume sessions after a clean quit, and it cannot send the API token (it works in the default `FRIDAY_AUTH=auto` mode from the same machine).
- The Node CLI (`bin/*.js`) was not in the shared source and was not tested. In `FRIDAY_AUTH=required` mode it needs to send the token.
- No browser screen for managing memories yet (API plus the `remember:` chat command only).
- The vector index is in memory (about chunks × dimensions × 4 bytes, e.g. 100k chunks × 768 ≈ 300 MB). Move to `sqlite-vec` beyond personal scale. Rate limits are per process.
- The vision path is tested with a mocked model; OCR is tested for real.
- Saving a partial reply when the client disconnects is implemented but not covered by an automated test.
- The legacy `/api/agent/stream` loop has no session persistence. Its approvals, permissions and shared tools are unchanged.
- The shell denylist is not a sandbox. Use Docker for untrusted repositories.
- `main.cpp` (C++ vector prototype) remains isolated and unused.

