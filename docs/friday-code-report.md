# Friday Code — Master Prompt 2 report

Scope: the terminal chat (`friday_agent/terminal.py`, launched by `friday chat`) and the agent backend it uses.
Friday Browser (`src/`, `index.html`, `main.cpp`) and the dashboard (`backend/app/main.py`, `src/app/dashboard/`) are untouched: a full diff against the original v4.00 tree shows no changes there.

## 1. Architecture (audited)

```
Terminal (Textual app, friday_agent/terminal.py)
  └─ HTTP + SSE → backend/app/agent_routes.py   POST /api/agent/sessions/{id}/turns
       └─ friday_agent/engine.py  run_turn(): tool loop, approvals, hooks, plan mode
            ├─ friday_agent/llm.py   chat_stream() → Ollama /api/chat (stream, tools, keep_alive)
            ├─ friday_agent/tools.py 24 tools (files, grep/glob, bash, git, web_fetch, subagents, search_knowledge)
            └─ friday_agent/session_store.py  JSON persistence of sessions
```

* **Local model and runtime:** Ollama (`OLLAMA_HOST`, default 127.0.0.1:11434), default model `llama3.2` (3B, Q4_K_M GGUF, ~2 GB), `num_ctx 8192`, `num_predict 4096`, `keep_alive 30m`. Unchanged. No external AI API was added; everything stays on the machine.
* RAG, embeddings and the vector store belong to the browser chat path (`backend/app/main.py`). The terminal agent reaches them only through the `search_knowledge` tool, so they were not on the terminal's slow path.

## 2. Root causes (measured, not guessed)

These were measured with Ollama 0.40.16, the real `llama3.2` model, on a 2-core CPU with 7.8 GB RAM and no GPU. Ollama's own timing counters confirm them.

**Slow responses**
1. **llama3.2's chat template appends every tool schema to the newest question.** The engine sent all 24 schemas on every step (~1,680 tokens). Every new question therefore re-evaluated ~1,400–1,600 prompt tokens before the first word: 32–40 s at ~44 tok/s on this CPU. Ollama's prompt cache could not help, because that block moves to each new message.
2. **No warm-up.** The first question also paid for model load and system-prompt evaluation.

**Unanswered or "no answer" questions**
1. **The same template tells the model to "respond with a JSON for a function call".** Plain questions came back as JSON. "What is 2 + 2?" returned `{"name":"add",...}` (a tool that doesn't exist), and "write is_palindrome" returned an `edit_file` call printed as text. Users saw a JSON blob, not an answer.
2. **Other small-model failure shapes:** malformed JSON calls (`"parameters{"path"...`), prose that ends in a JSON call, echoed tool *schemas*, and empty replies. All of these were shown as the final answer or as nothing.
3. **Cancelled turns stayed in history with no reply.** The next question was read as part of the cancelled one: after stopping an essay, "what is 3 + 4?" produced the essay (199 s).
4. **Context overflow:** Ollama silently drops the *start* of an over-long prompt, which loses the system prompt.
5. **Environment: the Ollama runner was OOM-killed.** Ollama 0.40's llama-server keeps a RAM prompt cache with an 8 GB default limit. It reached 6.1 GB RSS for a 2 GB model and the kernel killed it, so the next answer had to reload the model. See section 8.

## 3. Fixes (local inference; same model, same runtime)

| Fix | Where |
|---|---|
| **Tool routing** (terminal sessions only): plain questions are sent with no tool schemas; workspace questions get a read-only core (read_file, list_dir, glob, grep, git status/diff/log/show, ~510 tokens); actions get all 24 tools. If the model asks for a tool it wasn't given, that step is retried with all tools, so nothing is lost. `/tools auto\|on\|off` overrides it. Browser sessions keep the original behaviour (`tool_routing` defaults to `always`). | `routing.py`, `engine.py` |
| **Text tool-call recovery:** JSON calls printed as text, prose ending in a JSON call, OpenAI-style wrapped calls → executed as real calls. JSON-looking output is held back while streaming so it never flashes on screen. | `routing.py`, `engine.py` |
| **Re-ask, never fake:** calls to tools that don't exist, malformed calls, echoed schemas and empty replies are re-asked (up to 2 times) with a one-off correction that is never stored. If that still fails, the turn ends with a clear error. | `engine.py` |
| **Loop guard:** the same successful call 3× in a row, or 5× in one turn, is refused with an explanation. The existing guard for repeated failures is kept. | `engine.py` |
| **Context window:** the system prompt and the newest whole exchanges are always sent within the budget; the stored history is not modified. | `engine.window_messages` |
| **Warm-up:** at session start the model is loaded and the system prompt cached, in the background. | `llm.warm_up`, `agent_routes` |
| **Cancellation:** new `POST /api/agent/sessions/{id}/cancel` cancels the server-side turn, which closes the Ollama request. A cancelled turn is closed in history with its partial text plus "[interrupted by the user]". | `agent_routes.py`, `engine.py` |
| **Failed turns:** if nothing happened, the failed question is removed from model history (the terminal keeps it for retry); no error text is ever stored as an answer. | `engine.py` |
| **Diagnostics:** a `metrics` event per turn, plus `<FRIDAY_DATA_DIR>/diagnostics/agent_turns.jsonl` with time to first token, prompt and generated tokens, durations, tok/s, model load, tool time, retries, status and backend RSS. Summarise with `python -m friday_agent.diagnostics`. | `diagnostics.py` |

## 4. Benchmarks: same model, same hardware, same prompts

Before = original v4.00 backend. After = this version, session created the way the terminal creates it. Times are wall-clock from sending the question.

| Prompt | Before: first word / total | Before: what the user got | After: first word / total | After: what the user got |
|---|---|---|---|---|
| What is 2 + 2? | 54.4 s / 54.7 s | `{"name":"add",...}` | **1.6 s / 1.8 s** | `4` |
| Process vs thread | 41.2 s / 50.2 s | answer (after a needless search_knowledge) | **1.2 s / 14.4 s** | answer |
| Write is_palindrome | 53.2 s / 53.5 s | `{"name": "edit_file", ...}` | **1.7 s / 10.1 s** | the Python function |
| Which file defines the Ollama client? | 56.0 s / 62.3 s | tried `edit_file` | **22.0 s / 37.8 s** | ran glob, answered in prose |
| Follow-up on that | 42.6 s / 50.6 s | asked for network permission | 20.2 s / 42.3 s | ran git_show, answered in prose |
| Cancel an essay, then "3 + 4?" | next answer after 41 s | `{"name": "math", ...}` | **cancel 0.14–0.19 s, next answer after 1.3–1.9 s** | `7` |

Extended runs (after):
* 12 consecutive questions in one session: first word at 0.8–1.1 s throughout.
* 344-token answer: first word 1.2 s, 3.8 tok/s.
* Two sessions at once: both complete (Ollama serves them in turn).
* Ollama down: a clear error in 0.02 s, never stuck on loading.

Generation speed (3.8–4.8 tok/s) is this CPU's limit and was not changed. Answer *quality* on workspace questions is the 3B model's limit: it often chooses poor search patterns. A larger local model (for example `qwen2.5-coder:7b`, via `/model`) would help if your hardware allows; the model was not changed.

## 5. Friday Code terminal

* **Branding:** "Friday Chat" → **Friday Code** (title, header, welcome, block-letter `FRIDAY / CODE` wordmark with an offset shadow, after the reference). Internal module, API and folder names are unchanged.
* **Palette, sampled from the reference screenshots:** background `#23212c`, terracotta `#d87756` (logo, accents), rose `#d78788` (title, prompt glyph), selection blue `#b4c6de`, text `#f0f0f6`, muted `#84848a`, dotted rules `#66666c`, code theme Dracula (`#282a36`). Centralised in `THEMES` with a matching Rich Markdown theme. The bright "ember" orange was retuned to the same warm family; `midnight` and `forest` remain available via `/theme`.
* **Layout:** header, dotted rule, scene, captioned dotted rule, transcript, thinking line, rounded composer, slash-command dropdown, status bar. The side panel (task, progress, tools, context bar, model speed, git, commands) shows on terminals ≥150 columns; F2 toggles it.
* **Messages:** user prompts on a raised band with `❯`; replies with `●` and Markdown, with syntax-highlighted code blocks; tool calls as `● Read(path)` with a `⎿` result line, timing and diffs; a footer with time, first-word latency and tok/s.
* **Request lifecycle:** submitted → processing → running tool → generating → completed / failed / cancelled, shown in the status bar and in "✻ Thinking… (12s · esc to interrupt)".
  * A silent backend is failed after 360 s (`FRIDAY_STALL_TIMEOUT`).
  * Failures show the real error, put the question back in the prompt, and enable **Ctrl+R** / `/retry`.
* **Controls:** **Esc** stops generation (real server-side cancel) or closes the menu. **Ctrl+Y** / `/copy` copies the last code block (`/copy all` copies the whole reply). A `/` dropdown filters commands (↑/↓, Tab, Enter). `/stats` shows the speed of the last answer and toggles detailed timings.

## 6. Mascot and environment

* **Patch:** an original pixel robot (cream shell, dark visor with mint eyes, antenna light, chest light). It is not based on Claude's character. It walks, blinks, breathes and turns around.
* **Seasonal interactions:** kicks leaves, catches snow, splashes in puddles, smells flowers, wipes its brow.
* **Reactions to chat activity:** a hop and "!" when a question is sent; stands still with "…" dots and a fast antenna blink while generating; happy eyes and a sparkle when done; a worried look and a sweat drop on error.
* **Four seasons:**
  * Spring: sunflowers, coloured flowers, growing crop rows, a blossom tree.
  * Summer: a deep-green tree, bushes, sun; straw hat and sunglasses.
  * Autumn: an orange and gold tree, falling leaves, leaf litter, pumpkins; a mustard scarf.
  * Winter: snow ground, a bare snowy tree, a snowman; beanie, scarf and sweater.
* **Six weathers:**
  * Sunny: sun with rays.
  * Cloudy: drifting dithered clouds.
  * Rainy: raindrops, splashes, puddles, a darker wet ground; Patch wears a raincoat and holds an umbrella.
  * Snowy: falling snow.
  * Windy: fast clouds, swaying plants, blowing leaves or petals, a fluttering scarf.
  * Clear.
* **Weather by season:** only sensible pairs (snow only in winter). Clothing is derived from season and weather, so it always matches.
* **Day and night:** follows the computer's local time with smooth dawn and dusk. The sun and moon move across the sky; at night there are stars and a crescent moon, and Patch's eyes glow.
* **Every 10 minutes:** a new weather variation is chosen at random (never the same twice in a row) and cross-faded over 4 s, while Patch keeps walking.
  * Season follows a Northern Hemisphere calendar by default; `/scene cycle` lets the change pick seasons too.
  * The scene uses one 6 fps timer, renders in ~1.7 ms per frame, can't take focus, and never touches the input or the transcript.
* **Preview commands:** `/scene spring|summer|autumn|winter|sunny|…|day|night|next|auto|off`. F3 toggles the scene; it hides itself on terminals shorter than 32 rows.

## 7. Tests

`pytest` (backend + agent + terminal): **121 passed, 1 skipped** (baseline 88 passed, 1 skipped).

New suites:
* `tests/test_friday_code_reliability.py` — routing, all recovery paths, the loop guard, the context window, API routing and real cancellation.
* `tests/test_friday_code_scene.py` — calendar, daylight, 10-minute schedule with no repeats, cycle mode, compatible pairs, cross-fade, outfits, render size and cost, reactions.
* `tests/test_friday_code_terminal.py` — the real app against the real API router: answer lifecycle, copy, failure → question restored → Ctrl+R retry, backend unreachable, dropdown, scene commands, a single animation timer.

One existing assertion in `tests/test_friday_agent_smoke.py` was updated: it pinned the old indicator wording ("Thought for 3s"), which the redesign intentionally replaces.

Live runs of the real terminal (headless, real model) were also done: answer, code generation, copy, dropdown, cancel mid-stream, answer after cancel, workspace tool question, error state, and six scene variations.

## 8. Remaining issues and environment notes

* **Ollama prompt-cache memory (recommended fix):** on Ollama 0.40.x the runner's prompt cache can grow until the system kills it. Capping it fixed this in testing (log shows `size limit: 1024 MiB`). On Windows, run `setx LLAMA_ARG_CACHE_RAM 1024`, then quit and restart Ollama. On Linux/macOS, `export LLAMA_ARG_CACHE_RAM=1024` before `ollama serve`. This was not changed automatically because it is your system configuration.
* **Cold start:** the first question right after Ollama starts still waits for the model to load (seconds to over a minute on slow CPUs). The warm-up starts that load as soon as the terminal opens.
* **Not in the uploaded zip:** `bin/chat.js` and `bin/banner.js` (the Node launcher files). If `friday chat` prints its own banner before starting `terminal.py`, that text still needs renaming there.
* **Not tested:**
  * GPU/VRAM: no GPU was available here.
  * Windows Terminal rendering: tested via Textual's renderer on Linux. Half-block and box glyphs are standard in Cascadia Mono.
  * The low-memory case beyond the OOM finding above.
* **Quality ceiling:** llama3.2 3B answers general questions well and fast now, but is unreliable at multi-step repository work. A larger local coder model is the remaining lever.
