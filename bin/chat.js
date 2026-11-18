"use strict";
/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Terminal chat for FRIDAY. Talks to the existing FastAPI backend
 * (/api/chat/stream, /api/conversations, ...) — same data as the web UI.
 * No dependencies (Node 20+ built-in fetch + readline).
 */
const readline = require("readline");

const tty = process.stdout.isTTY;
const paint = (n, s) => (tty ? `\x1b[${n}m${s}\x1b[0m` : s);
const dim = (s) => paint(90, s),
  cyan = (s) => paint(36, s),
  green = (s) => paint(32, s);
const red = (s) => paint(31, s),
  yellow = (s) => paint(33, s),
  bold = (s) => paint(1, s);

const HELP = `
  ${bold("Commands")}
  /new              start a new conversation
  /history          list recent conversations
  /resume <n|id>    continue a conversation from /history
  /docs             list indexed documents
  /status           show Ollama / model status
  /sources on|off   show or hide source references (default on)
  /k <1-20>         number of chunks to retrieve
  /clear            clear the screen
  /help             show this help
  /exit             quit  (Ctrl+C at an empty prompt also quits)

  End a line with \\ to continue on the next line.
  While FRIDAY is answering, Ctrl+C stops the answer.
`;

async function start({ api, args = [] }) {
  const state = { convId: null, k: null, showSources: true, history: [] };
  let controller = null;

  const call = async (method, path, body) => {
    const res = await fetch(api + path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok)
      throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);
    return res.json();
  };

  async function ask(question) {
    controller = new AbortController();
    let failed = false;
    process.stdout.write("\n" + green("friday › "));
    let res;
    try {
      res = await fetch(api + "/api/chat/stream", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: controller.signal,
        body: JSON.stringify({
          question,
          conversation_id: state.convId || undefined,
          k: state.k || undefined,
        }),
      });
      if (!res.ok)
        throw new Error(`${res.status} ${(await res.text()).slice(0, 200)}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i);
          buf = buf.slice(i + 2);
          for (const line of block.split("\n")) {
            if (!line.startsWith("data: ")) continue;
            let msg;
            try {
              msg = JSON.parse(line.slice(6));
            } catch {
              continue;
            }
            if (msg.token) process.stdout.write(msg.token);
            if (msg.error) {
              failed = true;
              process.stdout.write("\n" + red(msg.error));
            }
            if (msg.done) {
              state.convId = msg.conversation_id || state.convId;
              const seen = new Set();
              const refs = (msg.sources || [])
                .map((s) => `${s.title}#${s.chunk}`)
                .filter((r) => !seen.has(r) && seen.add(r));
              if (state.showSources && refs.length)
                process.stdout.write(
                  "\n" + dim("  sources: " + refs.join(", ")),
                );
            }
          }
        }
      }
      process.stdout.write("\n\n");
      return !failed;
    } catch (e) {
      if (e.name === "AbortError")
        process.stdout.write(dim("\n  (stopped)\n\n"));
      else process.stdout.write("\n" + red("  error: " + e.message) + "\n\n");
      return false;
    } finally {
      controller = null;
    }
  }

  async function command(line) {
    const [name, ...rest] = line.trim().split(/\s+/);
    const arg = rest.join(" ");
    switch (name) {
      case "/help":
        console.log(HELP);
        break;
      case "/exit":
      case "/quit":
        return "exit";
      case "/clear":
        process.stdout.write("\x1b[2J\x1b[H");
        break;
      case "/new":
        state.convId = null;
        console.log(dim("  new conversation\n"));
        break;
      case "/status": {
        const s = await call("GET", "/api/status");
        console.log(
          `  ollama: ${s.ollama === "online" ? green("online") : red("offline")}   chat model: ${s.generation_model}   embeddings: ${s.embedding_model}   documents: ${s.documents}\n`,
        );
        break;
      }
      case "/docs": {
        const docs = await call("GET", "/api/documents");
        if (!docs.length)
          console.log(dim("  no documents yet — upload some in the web UI\n"));
        else {
          docs.forEach((d) =>
            console.log("  • " + (d.title || d.name || d.filename || d.id)),
          );
          console.log();
        }
        break;
      }
      case "/history": {
        state.history = (await call("GET", "/api/conversations")).slice(0, 15);
        if (!state.history.length) console.log(dim("  no conversations yet\n"));
        else {
          state.history.forEach((c, i) =>
            console.log(
              `  ${cyan(String(i + 1).padStart(2))}  ${c.title}  ${dim(c.updated_at || "")}`,
            ),
          );
          console.log(dim("\n  /resume <n> to continue one\n"));
        }
        break;
      }
      case "/resume": {
        if (!arg) {
          console.log(yellow("  usage: /resume <n|id>  (see /history)\n"));
          break;
        }
        const id =
          /^\d+$/.test(arg) && state.history[+arg - 1]
            ? state.history[+arg - 1].id
            : arg;
        const conv = await call(
          "GET",
          "/api/conversations/" + encodeURIComponent(id),
        );
        state.convId = conv.id;
        console.log(bold("\n  " + conv.title) + "\n");
        for (const m of conv.messages)
          console.log(
            (m.role === "user" ? cyan("you › ") : green("friday › ")) +
              m.content +
              "\n",
          );
        break;
      }
      case "/sources":
        state.showSources = arg !== "off";
        console.log(dim(`  sources ${state.showSources ? "on" : "off"}\n`));
        break;
      case "/k": {
        const n = parseInt(arg, 10);
        if (n >= 1 && n <= 20) {
          state.k = n;
          console.log(dim(`  retrieving ${n} chunks\n`));
        } else console.log(yellow("  usage: /k <1-20>\n"));
        break;
      }
      default:
        console.log(yellow(`  unknown command ${name} — try /help\n`));
    }
  }

  // ---- one-shot mode: friday chat -p "question" ----
  const p = args.findIndex((a) => a === "-p" || a === "--print");
  if (p >= 0) {
    const q = args
      .slice(p + 1)
      .filter((a) => !a.startsWith("-"))
      .join(" ")
      .trim();
    if (!q) {
      console.error('usage: friday chat -p "your question"');
      return 1;
    }
    state.showSources = false;
    return (await ask(q)) ? 0 : 1;
  }

  // ---- interactive REPL ----
  let status = "";
  try {
    const s = await call("GET", "/api/status");
    status = `${s.generation_model} · ollama ${s.ollama} · ${s.documents} docs`;
  } catch {}
  console.log("\n" + dim(status));
  console.log(dim("  /help for commands · Ctrl+C to quit\n"));

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: cyan("you › "),
  });
  let pending = "";
  let queue = Promise.resolve();
  let inputClosed = false;
  let exiting = false;

  rl.on("SIGINT", () => {
    if (controller) controller.abort();
    else {
      console.log();
      exiting = true;
      rl.close();
    }
  });

  const processLine = async (raw) => {
    if (exiting) return;
    if (raw.endsWith("\\")) {
      pending += raw.slice(0, -1) + "\n";
      process.stdout.write(dim("  … "));
      if (!inputClosed && !rl.closed) rl.prompt();
      return;
    }

    const line = (pending + raw).trim();
    pending = "";
    if (line) {
      if (line.startsWith("/")) {
        if ((await command(line)) === "exit") {
          exiting = true;
          rl.close();
          return;
        }
      } else if (line.length > 4000) {
        console.log(red("  message too long (max 4000 characters)\n"));
      } else {
        await ask(line);
      }
    }
    if (!inputClosed && !rl.closed) rl.prompt();
  };

  const closed = new Promise((resolve) => {
    rl.on("close", () => {
      inputClosed = true;
      queue.then(
        () => resolve(0),
        () => resolve(0),
      );
    });
  });

  rl.on("line", (raw) => {
    queue = queue
      .then(() => processLine(raw))
      .catch((e) => {
        console.log(red("  error: " + e.message) + "\n");
        if (!inputClosed && !rl.closed) rl.prompt();
      });
  });

  rl.prompt();
  return closed;
}

module.exports = { start };
