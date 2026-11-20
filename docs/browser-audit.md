# Friday Browser — Audit Report (Master Prompt 1)

Date: 20 Nov 2026 · Scope: Friday Browser only. The Claude Code reference design was **not** applied to the browser.

> Historical snapshot from before the standalone chat product was removed. Its Chat UI, terminal chat client, and chat/conversation API references below do not describe the current project. Current architecture is documented in [architecture.md](architecture.md).

## 1. Project architecture findings

| Part                        | Location                                                                       | What it is                                                                 |
| --------------------------- | ------------------------------------------------------------------------------ | -------------------------------------------------------------------------- |
| **Friday Browser** (web UI) | `src/app/` (Next.js 16, React 19)                                              | The browser workspace launched by `friday` / `friday browser` on :3000     |
| **Friday Chat** (Terminal)  | `friday_agent/terminal.py` (Textual + Rich), `bin/chat.js`, `bin/banner.js`    | Full-screen terminal console; has its own Textual `CSS` string and `Theme` |
| Backend (shared)            | `backend/app/main.py`, `backend/app/agent_routes.py`, `friday_agent/engine.py` | FastAPI on :8000, SQLite, Ollama                                           |
| Not part of either          | `index.html` + `main.cpp` + `httplib.h`                                        | Standalone C++ VectorDB prototype (:8081), per `docs/architecture.md`      |

Browser internals:

- **Entry point:** `src/app/layout.tsx` (fonts, theme bootstrap, `globals.css`) → `src/app/page.tsx` (whole workspace)
- **Components:** `ui/Dashboard.tsx`, `ui/ChatView.tsx` (browser chat view), `ui/Views.tsx` (Documents, Search, Collections, Memory, Activity, History, Settings), `ui/charts.tsx`, `ui/Logo.tsx`, `ui/WorkingStatus.tsx`, `agent/AgentPanel.tsx` + `agent/useAgent.ts`, `thinking/ThinkingIndicator.tsx`, `login/page.tsx`
- **Stylesheets:** `globals.css` (design tokens, light/dark), `ui/ui.css` (workspace), `agent/agent.css` (all `.agent-*`), `thinking/thinking.css` (`.think-*`), `login/login.css` (`.login*`)
- **Navigation / toolbar:** sidebar `NAV` list plus header actions in `page.tsx`. Views are in-memory state (`useState<View>`); there is no URL routing and no browser-style tabs.
- **State:** all workspace state lives in `page.tsx` hooks. The agent panel has its own state in `useAgent.ts`.
- **Backend communication:** `ui/api.ts` (`api()` helper, `NEXT_PUBLIC_API_URL`, default `http://localhost:8000`). Chat SSE and uploads use `fetch` directly in `page.tsx`.
- **Persistence and settings:** theme in `localStorage["friday-theme"]` (`ui/theme.ts`); everything else server-side (`/api/settings`, conversations, documents, memories, collections in SQLite). Agent session id in `useAgent.ts` storage.
- **Dashboard dependencies:** `/api/activity`, `/api/conversations`, `/api/documents`, `/api/memories`, `/api/agent/sessions`, `/api/health`, `/api/collections`, plus `charts.tsx` and `perDay`/`timeAgo` in `api.ts`.

## 2. Browser ↔ Chat boundaries

- **No shared UI code or CSS.** The terminal renders with Textual/Rich, so browser CSS cannot reach it and its Textual CSS cannot reach the browser. No front-end component is shared.
- **Shared surface = backend only:**
  - Agent API `/api/agent/*`: the terminal console and the browser Agent panel share sessions.
  - **The Dashboard reads `GET /api/agent/sessions`** fields `id, full_id, workspace, busy, mode, status, title, messages`. A terminal-side change to this response would break the Dashboard's _Agent runs_ card and _Agent sessions_ stat. It is now pinned by a test (see §4).
  - Conversations and documents (`/api/chat/stream`, `/api/conversations`): shared with `bin/chat.js`.
- **Rule for the Friday Chat redesign:** keep it inside `friday_agent/terminal.py` (and `bin/*.js` if needed). Do not touch `src/app/**` or change the response shape of the shared endpoints.

## 3. Browser issues identified (all tested, not assumed)

Tested live with headless Chrome against the running backend and production build. 27 checks.

| #   | Finding                                                                                                                                                                  | Verdict                                                                                                                    |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| 1   | A failed chat request (e.g. Ollama offline) is labelled **"Stopped after Ns"**, the same label as a user-pressed Stop, so a failure looks like a cancellation.           | **Bug — fixed**                                                                                                            |
| 2   | `ui.css` contains a bare global `kbd { … }` rule that styles every `<kbd>` wherever the stylesheet is loaded.                                                            | **Isolation gap — fixed**                                                                                                  |
| 3   | Nothing protected the session-list fields the Dashboard reads from changes made for the terminal.                                                                        | **Isolation gap — test added**                                                                                             |
| 4   | Refresh always returns to Home, and browser Back/Forward don't move between views (view isn't in the URL).                                                               | By design (single-page workspace). **Not changed**: adding URL routing would change behaviour and architecture. Your call. |
| 5   | A chat that fails (Ollama offline) is not saved to Recents/History.                                                                                                      | Intended: the backend only persists completed exchanges.                                                                   |
| 6   | `thinking/thinking.css` declares `--think-*` variables on `:root`, plus a duplicate `prefers-color-scheme: dark` block with identical values.                            | Harmless, no visual effect. Left alone (no unrelated refactoring).                                                         |
| 7   | Dashboard Activity chart: the last x-axis date label is partly covered by the end-point dot.                                                                             | Cosmetic, pre-existing. Left alone (dashboard preservation).                                                               |
| —   | Not applicable: address bar, browser tabs, back/forward buttons and refresh button. Friday Browser is a workspace UI, not a web browser, and has none of these controls. | —                                                                                                                          |

Everything else worked: launch, all 9 sidebar views, Ctrl+K, Ctrl+Shift+O, theme toggle and its persistence, upload → indexing → ready, search, collections, memories, settings save, error banner, mobile menu and scrim, no console errors.

## 4. Fixes implemented

1. **`src/app/ui/ChatView.tsx`**: the status line reads **"Failed after Ns"** when the request errored, and still reads **"Stopped after Ns"** when you press Stop. Text only; no styling changed.
2. **`src/app/ui/ui.css`**: `kbd { … }` → `:where(.app) kbd { … }`. This scopes the rule to the browser workspace. `:where()` adds zero specificity, so rules like `.side-new kbd` win exactly as before.
3. **`backend/tests/test_browser_contract.py`** (new): pins the `/api/agent/sessions` fields and `/api/health` fields the Dashboard and sidebar read.

## 5. Modified file list

- `src/app/ui/ChatView.tsx` (+4 / −1)
- `src/app/ui/ui.css` (+2 / −1)
- `src/app/globals.css`, `src/app/layout.tsx`, `src/app/ui/Views.tsx`: colour palette (see §9)
- `docs/browser-audit.md` (this report, new)
- `backend/tests/test_browser_contract.py` (new, 47 lines)

No dependencies added. No layout, colour, typography, spacing or architecture changes.

## 6. Dashboard preservation

Screenshots were taken before and after on identical fresh data and compared pixel by pixel:

| Screen                                                                        | Result                                                                    |
| ----------------------------------------------------------------------------- | ------------------------------------------------------------------------- |
| Dashboard — light                                                             | Identical                                                                 |
| Dashboard — dark                                                              | Identical                                                                 |
| Home, Chat, Agent, Documents, Search, Collections, Memory, Activity, Settings | Identical                                                                 |
| Mobile menu                                                                   | Identical                                                                 |
| Chat after a failed request                                                   | Only the status label changed ("Stopped" → "Failed"), in a 108×13 px area |

## 7. Regression test results

- Backend + agent tests: **86 passed, 1 skipped** before → **88 passed, 1 skipped** after (2 new contract tests).
- `next build` + TypeScript: pass, before and after.
- Live browser audit: same 25/27 before and after. The two non-passes are items 4 and 5 above (by design).

## 8. Remaining issues / notes

- Items 4, 6 and 7 above are unchanged pending your decision.
- The uploaded dump has no `.js`/`.mjs` files (`bin/friday.js`, `bin/chat.js`, `postcss.config.mjs`, `eslint.config.mjs`, `public/`). The launcher and `bin/chat.js` were not audited. For the build test I used a temporary standard Tailwind v4 PostCSS config, which is not part of the deliverable. ESLint was not run.
- Chat was tested with Ollama offline only (no model in the test environment). Streaming with a live model was not exercised end to end; backend streaming tests cover it.
- Outside scope: `index.html` (VectorDB prototype) calls `DELETE /delete/{id}`, which `main.cpp` does not define.

## 9. Approved follow-up: Claude desktop colour palette (20 Nov 2026)

On your instruction (which supersedes the "keep colours" rule in Master Prompt 1), the browser's white/blue palette was replaced with Claude desktop's warm palette. **Colours only**: layout, spacing, typography, components and behaviour are unchanged.

| Token                              | Light                                     | Dark                                      |
| ---------------------------------- | ----------------------------------------- | ----------------------------------------- |
| Background `--bg`                  | `#faf9f5`                                 | `#262624`                                 |
| Cards `--surface`                  | `#ffffff`                                 | `#30302e`                                 |
| Sunken / hover                     | `#f0eee6`                                 | `#1f1e1d` / `#3a3a37`                     |
| Borders `--line` / `--line-strong` | `#e8e6dc` / `#dedcd1`                     | `#3a3935` / `#4a4943`                     |
| Text / secondary / muted           | `#141413` / `#3d3d3a` / `#73726c`         | `#faf9f5` / `#c2c0b6` / `#9c9a92`         |
| Accent / accent-2                  | `#c6613f` / `#d97757`                     | `#d97757` / `#e08a6d`                     |
| Chart series                       | `#d97757 #6a9bcc #788c5d #c9a26b #a68ac2` | `#d97757 #6a9bcc #8fa672 #d4b07a #b49ad0` |

Files changed: `src/app/globals.css` (token values only), `src/app/layout.tsx` (browser `themeColor`), `src/app/ui/Views.tsx` (Settings theme swatches).
Verification: production build passes, backend 88 passed / 1 skipped, live browser audit unchanged (25 of 27, the same two by-design items as §3).
