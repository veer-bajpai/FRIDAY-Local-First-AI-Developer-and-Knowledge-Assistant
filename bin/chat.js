#!/usr/bin/env node
/*
 * FRIDAY terminal chat — "Classic Mono" design.
 * Dependency-free (Node 20+). Talks to the same backend as the web app:
 *   POST /api/chat/stream (SSE: {meta} {token}... {done}|{error}),
 *   /api/conversations, /api/documents, /api/status.
 *
 *   node bin/chat.js                 interactive chat
 *   node bin/chat.js -p "question"   ask once and exit
 *   node bin/chat.js --api http://127.0.0.1:8000
 *
 * Env: FRIDAY_API_URL, FRIDAY_API_TOKEN, FRIDAY_DATA_DIR, FRIDAY_NO_BANNER=1, NO_COLOR.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const readline = require("readline");

// ───────────────────────────── style ─────────────────────────────
const TTY = Boolean(process.stdout.isTTY);
const COLOR = TTY && !process.env.NO_COLOR && process.env.TERM !== "dumb";
const esc = (code) => (s) => (COLOR ? `\x1b[${code}m${s}\x1b[0m` : String(s));
const rgb = (r, g, b) => esc(`38;2;${r};${g};${b}`);
const c = {
  accent: rgb(255, 148, 40), // FRIDAY orange
  text: rgb(212, 212, 216),
  muted: rgb(130, 130, 138),
  faint: rgb(86, 86, 92),
  rule: rgb(58, 58, 62),
  ok: rgb(127, 191, 127),
  err: rgb(232, 110, 100),
  code: rgb(232, 192, 125),
  kw: rgb(158, 203, 255),
  bold: esc("1"),
  italic: esc("3"),
};
const ANSI = /\x1b\[[0-9;]*m/g;
const visible = (s) => s.replace(ANSI, "").length;
const width = () => Math.max(40, Math.min((process.stdout.columns || 90) - 2, 100));
const out = (s = "") => process.stdout.write(s + "\n");

function wrap(text, indent, first = indent) {
  const max = width() - visible(indent);
  const words = text.split(/( +)/);
  const lines = [];
  let line = "";
  for (const w of words) {
    if (visible(line) + visible(w) > max && visible(line) > 0) {
      lines.push(line.replace(/ +$/, ""));
      line = /^ +$/.test(w) ? "" : w;
    } else line += w;
  }
  lines.push(line);
  return lines.map((l, i) => (i === 0 ? first : indent) + l).join("\n");
}

function inline(s) {
  return s
    .replace(/`([^`]+)`/g, (_, x) => c.code(x))
    .replace(/\*\*([^*]+)\*\*/g, (_, x) => c.bold(x))
    .replace(/(^|[^*\w])\*([^*\s][^*]*)\*(?=[^*\w]|$)/g, (_, p, x) => p + c.italic(x))
    .replace(/_\[([^\]]+)\]_/g, (_, x) => c.muted(`[${x}]`))
    .replace(/\[([^\]]+)\]\((https?:[^)]+)\)/g, (_, t, u) => `${t} ${c.faint(u)}`);
}

// Markdown → terminal, one completed line at a time (so it works while streaming).
class Renderer {
  constructor(pad = "  ") {
    this.pad = pad;
    this.fence = null;
    this.first = true;
  }
  line(raw) {
    const pad = this.pad;
    const lead = this.first ? `${c.accent("◆")} ` : pad;
    const fence = raw.match(/^\s*```\s*([\w+#.-]*)/);
    if (fence) {
      if (this.fence === null) {
        this.fence = fence[1] || "code";
        const label = ` ${this.fence} `;
        if (this.first) out(c.accent("◆"));
        out(pad + c.faint("┌" + label + "─".repeat(Math.max(4, Math.min(48, width() - 12) - label.length))));
      } else {
        this.fence = null;
        out(pad + c.faint("└" + "─".repeat(Math.max(4, Math.min(48, width() - 12)))));
      }
      this.first = false;
      return;
    }
    if (this.fence !== null) {
      out(pad + c.faint("│ ") + c.kw(raw));
      return;
    }
    if (!raw.trim()) {
      if (!this.first) out("");
      return;
    }
    let m;
    if ((m = raw.match(/^(#{1,6})\s+(.*)/))) {
      out(wrap(c.bold(c.text(inline(m[2]))), pad, lead));
    } else if ((m = raw.match(/^(\s*)[-*+]\s+(.*)/))) {
      const deep = pad + "  ".repeat(Math.min(3, Math.floor(m[1].length / 2)));
      out(wrap(inline(m[2]), deep + "  ", (this.first ? lead : deep) + c.muted("• ")));
    } else if ((m = raw.match(/^(\s*)(\d+)[.)]\s+(.*)/))) {
      const n = `${m[2]}. `;
      out(wrap(inline(m[3]), pad + " ".repeat(n.length), (this.first ? lead : pad) + c.muted(n)));
    } else if ((m = raw.match(/^>\s?(.*)/))) {
      out(wrap(c.muted(inline(m[1])), pad + c.rule("│ "), (this.first ? lead : pad) + c.rule("│ ")));
    } else if (/^\s*([-*_])\1{2,}\s*$/.test(raw)) {
      out(pad + c.rule("─".repeat(Math.min(40, width() - 4))));
    } else {
      out(wrap(inline(raw), pad, lead));
    }
    this.first = false;
  }
  end() {
    if (this.fence !== null) this.line("```");
  }
}

// ───────────────────────────── api ─────────────────────────────
function readToken() {
  if (process.env.FRIDAY_API_TOKEN) return process.env.FRIDAY_API_TOKEN.trim();
  const dirs = [process.env.FRIDAY_DATA_DIR, path.join(__dirname, "..", "data"), path.join(process.cwd(), "data")];
  for (const d of dirs) {
    if (!d) continue;
    try {
      return fs.readFileSync(path.join(d, "api_token"), "utf8").trim();
    } catch {}
  }
  return "";
}

class Api {
  constructor(base) {
    this.base = String(base || "http://127.0.0.1:8000").replace(/\/+$/, "");
    const token = readToken();
    this.headers = { "Content-Type": "application/json", ...(token ? { "X-Friday-Token": token } : {}) };
  }
  async json(method, url, body) {
    const res = await fetch(this.base + url, {
      method,
      headers: this.headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {}
      throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    }
    return res.json();
  }
  async *stream(body, signal) {
    const res = await fetch(this.base + "/api/chat/stream", {
      method: "POST",
      headers: this.headers,
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok || !res.body) {
      let detail = res.statusText;
      try {
        detail = (await res.json()).detail || detail;
      } catch {}
      throw new Error(`${res.status} ${typeof detail === "string" ? detail : JSON.stringify(detail)}`);
    }
    const decoder = new TextDecoder();
    let buf = "";
    for await (const chunk of res.body) {
      buf += decoder.decode(chunk, { stream: true });
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const frame = buf.slice(0, i);
        buf = buf.slice(i + 2);
        const data = frame
          .split("\n")
          .filter((l) => l.startsWith("data:"))
          .map((l) => l.slice(5).trimStart())
          .join("\n");
        if (data) {
          try {
            yield JSON.parse(data);
          } catch {}
        }
      }
    }
  }
}

// ───────────────────────────── spinner ─────────────────────────────
class Spinner {
  constructor() {
    this.timer = null;
  }
  start() {
    if (!TTY) return;
    const frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];
    const words = ["Thinking", "Searching your documents", "Reading context", "Writing"];
    const t0 = Date.now();
    let i = 0;
    process.stdout.write("\x1b[?25l");
    this.timer = setInterval(() => {
      const s = (Date.now() - t0) / 1000;
      const word = words[Math.min(words.length - 1, Math.floor(s / 2.5))];
      process.stdout.write(`\r\x1b[2K${c.accent(frames[i++ % frames.length])} ${c.muted(word + "…")}  ${c.faint(s.toFixed(1) + "s")}`);
    }, 90);
  }
  stop() {
    if (!this.timer) return;
    clearInterval(this.timer);
    this.timer = null;
    process.stdout.write("\r\x1b[2K\x1b[?25h");
  }
}

// ───────────────────────────── chat ─────────────────────────────
const HELP = [
  ["/new", "start a new conversation"],
  ["/history", "list recent conversations"],
  ["/resume <n|id>", "continue a conversation"],
  ["/model [n|name]", "list installed models or switch"],
  ["/docs", "list your documents"],
  ["/status", "backend, model and document count"],
  ["/sources on|off", "show or hide sources under answers"],
  ["/k <1-20>", "how many passages to retrieve"],
  ["/clear", "clear the screen"],
  ["/exit", "quit (or ctrl+c)"],
];

class Chat {
  constructor(api) {
    this.api = api;
    this.conversationId = null;
    this.showSources = true;
    this.k = null;
    this.lastOk = true;
    this.history = [];
    this.busy = null;
  }

  async header() {
    if (process.env.FRIDAY_NO_BANNER === "1") return;
    let model = "offline";
    let docs = "?";
    let online = false;
    try {
      const s = await this.api.json("GET", "/api/status");
      model = s.generation_model || "model";
      docs = s.documents ?? "?";
      online = s.ollama === "online";
    } catch {}
    const dot = online ? c.ok("●") : c.err("●");
    out("");
    out(`${c.accent("▌FRIDAY")}${c.muted(`  ·  ${model}  ·  ${docs} docs  ·  `)}${dot}${c.muted(online ? " online" : " ollama offline")}`);
    out(c.rule("─".repeat(Math.min(width(), 72))));
    out("");
  }

  hint() {
    if (TTY) out(c.faint("  /help · /docs · /new · ctrl+c quit"));
  }

  async ask(question) {
    const spinner = new Spinner();
    const r = new Renderer();
    const controller = new AbortController();
    this.busy = controller;
    const t0 = Date.now();
    let line = "";
    let started = false;
    let done = null;
    let error = null;
    out("");
    spinner.start();
    try {
      const body = { question, conversation_id: this.conversationId };
      if (this.k) body.k = this.k;
      for await (const ev of this.api.stream(body, controller.signal)) {
        if (ev.token !== undefined) {
          if (!started) {
            spinner.stop();
            started = true;
          }
          line += ev.token;
          let i;
          while ((i = line.indexOf("\n")) >= 0) {
            r.line(line.slice(0, i));
            line = line.slice(i + 1);
          }
        } else if (ev.done) {
          done = ev;
        } else if (ev.error) {
          error = ev.error;
          if (ev.conversation_id) this.conversationId = ev.conversation_id;
        }
      }
    } catch (e) {
      error = e.name === "AbortError" ? "stopped" : friendly(e);
    } finally {
      spinner.stop();
      this.busy = null;
    }
    if (line) r.line(line);
    r.end();
    const secs = ((Date.now() - t0) / 1000).toFixed(1);
    if (done) {
      this.conversationId = done.conversation_id || this.conversationId;
      const sources = done.sources || [];
      if (this.showSources && sources.length) {
        out("");
        const list = sources
          .slice(0, 6)
          .map((s, n) => `${c.faint(`[${n + 1}]`)} ${c.muted((s.title || s.name || "source") + (s.page ? ` p.${s.page}` : ""))}`)
          .join("  ");
        out(wrap(list, "    ", `  ${c.faint("sources")}  `));
      }
      const extra = [done.model, done.note, done.truncated ? "reply hit the token limit" : null].filter(Boolean);
      out(`  ${c.faint(`answered in ${secs}s${extra.length ? " · " + extra.join(" · ") : ""}`)}`);
      this.lastOk = true;
    } else {
      out(`  ${error === "stopped" ? c.muted("stopped") : c.err("✗ " + (error || "no reply from FRIDAY"))}`);
      this.lastOk = false;
    }
    out("");
    return Boolean(done);
  }

  async command(input) {
    const [name, ...rest] = input.slice(1).trim().split(/\s+/);
    const arg = rest.join(" ");
    switch ((name || "").toLowerCase()) {
      case "help":
      case "?":
        out("");
        for (const [k, v] of HELP) out(`  ${c.text(k.padEnd(18))}${c.muted(v)}`);
        out("");
        return;
      case "new":
        this.conversationId = null;
        out(c.muted("  new conversation\n"));
        return;
      case "history": {
        this.history = (await this.api.json("GET", "/api/conversations")).slice(0, 15);
        out("");
        if (!this.history.length) out(c.muted("  no conversations yet"));
        this.history.forEach((h, i) => {
          const mark = h.id === this.conversationId ? c.accent("›") : " ";
          out(` ${mark}${c.faint(String(i + 1).padStart(3))}  ${c.text(trim(h.title || "Untitled", width() - 30))}  ${c.faint(when(h.updated_at))}`);
        });
        out(c.faint("\n  /resume <n> to continue one\n"));
        return;
      }
      case "resume": {
        if (!arg) return out(c.muted("  usage: /resume <n|id>\n"));
        if (!this.history.length) this.history = (await this.api.json("GET", "/api/conversations")).slice(0, 15);
        const pick = /^\d+$/.test(arg) && Number(arg) <= this.history.length ? this.history[Number(arg) - 1].id : arg;
        const conv = await this.api.json("GET", `/api/conversations/${encodeURIComponent(pick)}`);
        this.conversationId = conv.id;
        out(`\n  ${c.muted("resumed")} ${c.text(conv.title || "conversation")}\n`);
        for (const m of (conv.messages || []).slice(-4)) {
          if (m.role === "user") out(`${c.muted("›")} ${c.muted(trim(m.content, width() - 4))}`);
          else out(`${c.accent("◆")} ${c.faint(trim(m.content.replace(/\s+/g, " "), width() - 4))}`);
        }
        out("");
        return;
      }
      case "model":
      case "models": {
        const [tags, status] = await Promise.all([
          this.api.json("GET", "/api/models"),
          this.api.json("GET", "/api/status").catch(() => ({})),
        ]);
        const names = [...new Set((tags.models || []).map((m) => (typeof m === "string" ? m : m.name)).filter(Boolean))]
          .filter((n) => !/embed/i.test(n))
          .sort();
        const current = status.generation_model;
        if (!arg) {
          out("");
          if (!names.length) out(c.muted("  no models found — is Ollama running? try: ollama pull llama3.2"));
          names.forEach((n, i) => {
            const on = n === current;
            out(`  ${on ? c.accent("●") : " "}${c.faint(String(i + 1).padStart(3))}  ${on ? c.text(n) : c.muted(n)}`);
          });
          out(c.faint("\n  /model <number> or /model <name> to switch\n"));
          return;
        }
        const pick = /^\d+$/.test(arg) && Number(arg) >= 1 && Number(arg) <= names.length ? names[Number(arg) - 1] : arg;
        const saved = await this.api.json("PUT", "/api/settings", { model: pick });
        out(`  ${c.accent("●")} ${c.muted("model")} ${c.text(saved.model || pick)}\n`);
        return;
      }
      case "docs": {
        const docs = await this.api.json("GET", "/api/documents");
        out("");
        if (!docs.length) out(c.muted("  no documents yet — add some in the web app"));
        for (const d of docs.slice(0, 30)) {
          const state = d.status && d.status !== "ready" ? c.faint(` (${d.status})`) : "";
          out(`  ${c.faint("•")} ${c.text(trim(d.name || d.title || d.id, width() - 20))}${state}`);
        }
        if (docs.length > 30) out(c.faint(`  …and ${docs.length - 30} more`));
        out("");
        return;
      }
      case "status": {
        const s = await this.api.json("GET", "/api/status");
        out("");
        const row = (k, v) => out(`  ${c.muted(k.padEnd(12))}${c.text(v)}`);
        row("backend", this.api.base);
        row("ollama", s.ollama === "online" ? c.ok("online") : c.err(String(s.ollama)));
        row("model", String(s.generation_model));
        row("embeddings", String(s.embedding_model));
        row("documents", String(s.documents));
        row("sources", this.showSources ? "shown" : "hidden");
        row("k", this.k ? String(this.k) : "default");
        out("");
        return;
      }
      case "sources":
        if (!/^(on|off)$/i.test(arg)) return out(c.muted("  usage: /sources on|off\n"));
        this.showSources = arg.toLowerCase() === "on";
        return out(c.muted(`  sources ${this.showSources ? "shown" : "hidden"}\n`));
      case "k": {
        const n = Number(arg);
        if (!Number.isInteger(n) || n < 1 || n > 20) return out(c.muted("  usage: /k <1-20>\n"));
        this.k = n;
        return out(c.muted(`  retrieving ${n} passages\n`));
      }
      case "clear":
        process.stdout.write("\x1b[2J\x1b[H");
        return this.header();
      case "exit":
      case "quit":
      case "q":
        return "exit";
      default:
        out(c.muted(`  unknown command /${name} — try /help\n`));
    }
  }

  async interactive() {
    await this.header();
    this.hint();
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
      terminal: TTY,
      historySize: 200,
      completer: (line) => {
        const all = HELP.map(([k]) => k.split(" ")[0]);
        const hits = all.filter((k) => k.startsWith(line));
        return [line.startsWith("/") ? hits : [], line];
      },
    });
    const prompt = () => {
      rl.setPrompt((this.lastOk ? c.muted("› ") : c.err("› ")));
      rl.prompt();
    };
    let lastInterrupt = 0;
    rl.on("SIGINT", () => {
      if (this.busy) return this.busy.abort();
      if (rl.line) {
        rl.write(null, { ctrl: true, name: "u" });
        return;
      }
      if (Date.now() - lastInterrupt < 1500) return rl.close();
      lastInterrupt = Date.now();
      out(c.faint("\n  press ctrl+c again to quit"));
      prompt();
    });
    prompt();
    for await (const raw of rl) {
      const input = raw.trim();
      if (!input) {
        prompt();
        continue;
      }
      try {
        if (input.startsWith("/")) {
          if ((await this.command(input)) === "exit") break;
        } else {
          await this.ask(input);
        }
      } catch (e) {
        out(c.err(`  ✗ ${friendly(e)}\n`));
        this.lastOk = false;
      }
      prompt();
    }
    rl.close();
    out(c.faint("  bye"));
  }
}

function friendly(e) {
  const msg = String((e && e.message) || e);
  if (/ECONNREFUSED|fetch failed/i.test(msg) || (e && e.cause && e.cause.code === "ECONNREFUSED"))
    return "can't reach the FRIDAY backend — start it with `friday` and try again";
  if (/TimeoutError|aborted due to timeout/i.test(msg)) return "the backend took too long to answer";
  return msg;
}
const trim = (s, n) => (String(s).length > n ? String(s).slice(0, Math.max(1, n - 1)) + "…" : String(s));
function when(iso) {
  const t = Date.parse(iso);
  if (!t) return "";
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m}m ago`;
  if (m < 1440) return `${Math.round(m / 60)}h ago`;
  return new Date(t).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

// ───────────────────────────── entry ─────────────────────────────
function parseArgs(argv) {
  const opts = { api: process.env.FRIDAY_API_URL, prompt: null };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-p" || a === "--prompt" || a === "--print") opts.prompt = argv[++i] || "";
    else if (a === "--api" || a === "--url") opts.api = argv[++i];
    else if (a === "chat") continue;
    else if (opts.prompt === null && !a.startsWith("-") && i === argv.length - 1 && argv.includes("-p")) opts.prompt = a;
  }
  return opts;
}

/**
 * Start the chat. Accepts any of:
 *   run()                               read process.argv
 *   run(["-p", "question"])             argv array
 *   run({ api, prompt })                options (also apiUrl/baseUrl/url/port and p/question)
 *   run("http://127.0.0.1:8000", opts)  base URL first
 * Resolves with an exit code.
 */
async function run(a, b) {
  let opts;
  if (Array.isArray(a)) opts = parseArgs(a);
  else if (typeof a === "string" && /^https?:\/\//.test(a)) opts = { ...parseArgs([]), ...(b || {}), api: a };
  else if (a && typeof a === "object") opts = { ...parseArgs([]), ...a };
  else opts = parseArgs(process.argv.slice(2));
  if (b && typeof b === "object" && !Array.isArray(b) && typeof a !== "string") opts = { ...opts, ...b };
  const base = opts.api || opts.apiUrl || opts.baseUrl || opts.url || (opts.port ? `http://127.0.0.1:${opts.port}` : undefined);
  const prompt = opts.prompt ?? opts.p ?? opts.question ?? null;
  const chat = new Chat(new Api(base));
  if (prompt !== null && prompt !== undefined && prompt !== false) {
    if (!String(prompt).trim()) {
      out(c.err("✗ -p needs a question"));
      return 2;
    }
    return (await chat.ask(String(prompt))) ? 0 : 1;
  }
  await chat.interactive();
  return 0;
}

module.exports = { run, main: run, start: run, startChat: run, chat: run, runChat: run, default: run };

if (require.main === module) {
  run().then(
    (code) => process.exit(code || 0),
    (e) => {
      out(c.err(`✗ ${friendly(e)}`));
      process.exit(1);
    }
  );
}
