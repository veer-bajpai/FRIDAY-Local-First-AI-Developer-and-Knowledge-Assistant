"use strict";
/* eslint-disable @typescript-eslint/no-require-imports */
const readline = require("readline");

const THEMES = {
  midnight: { accent: 110, muted: 240, error: 131 },
  ember: { accent: 137, muted: 240, error: 131 },
  forest: { accent: 108, muted: 240, error: 131 },
};

async function start({ api, args = [] }) {
  const tty = process.stdout.isTTY && process.stdin.isTTY;
  const color = (code, value) =>
    tty && !process.env.NO_COLOR ? `\x1b[38;5;${code}m${value}\x1b[0m` : value;
  let themeName = ["midnight", "ember", "forest"].includes(
    process.env.FRIDAY_THEME,
  )
    ? process.env.FRIDAY_THEME
    : "midnight";
  let theme = THEMES[themeName];
  let mode = "default";
  let approvalResolver = null;
  let queue = Promise.resolve();
  let failed = false;
  const rl = readline.createInterface({ input: process.stdin, terminal: tty });

  const request = async (path, options = {}) => {
    const response = await fetch(api + path, {
      ...options,
      headers: {
        ...(options.body ? { "Content-Type": "application/json" } : {}),
        ...options.headers,
      },
    });
    if (!response.ok) {
      const body = await response.text();
      let detail = body;
      try {
        detail = JSON.parse(body).detail || body;
      } catch {}
      throw new Error(`${response.status} ${detail}`);
    }
    return response;
  };

  const json = async (path, options) => (await request(path, options)).json();
  const sendJson = (method, body) => ({ method, body: JSON.stringify(body) });
  const session = await json("/api/agent/sessions", sendJson("POST", {}));
  mode = session.mode;
  const sessionPath = `/api/agent/sessions/${session.id}`;

  let customCommands = {};
  try {
    customCommands = await json("/api/agent/commands");
  } catch {}

  const readApproval = (event) => {
    if (!tty) return Promise.resolve("n");
    return new Promise((resolve) => {
      const preview = event.preview ? `\n${event.preview}` : "";
      const args = JSON.stringify(event.arguments);
      process.stdout.write(
        `\n${color(theme.error, `Permission required: ${event.tool}`)} ` +
          `${args.slice(0, 500)}${args.length > 500 ? "…" : ""}${preview}\n` +
          `${color(theme.muted, "Allow? [y]es / [n]o / [a]lways: ")}`,
      );
      approvalResolver = resolve;
    });
  };

  const handleEvent = async (event) => {
    if (event.type === "turn_start") mode = event.mode || mode;
    if (event.type === "token") process.stdout.write(event.content);
    if (event.type === "tool_start") {
      const argsText = event.arguments?.command || event.arguments?.path || "";
      process.stdout.write(
        `\n${color(theme.muted, `[${event.tool}${argsText ? `: ${argsText}` : ""}]`)}\n`,
      );
    }
    if (event.type === "approval_required") {
      const answer = (await readApproval(event)).trim().toLowerCase();
      const decision =
        answer === "a" || answer === "always"
          ? "always"
          : answer === "y" || answer === "yes"
            ? "allow"
            : "deny";
      await json(
        `${sessionPath}/approvals/${event.id}`,
        sendJson("POST", { decision }),
      );
    }
    if (event.type === "tool_result") {
      mode = event.mode || mode;
      if (
        /^(Tool error:|Permission denied:|Blocked by|User declined)/.test(
          event.result || "",
        )
      ) {
        process.stdout.write(color(theme.error, `\n${event.result}\n`));
      }
    }
    if (event.type === "error") {
      failed = true;
      process.stdout.write(color(theme.error, `\n${event.message}\n`));
    }
    if (event.type === "done") mode = event.mode || mode;
  };

  const runTurn = async (prompt) => {
    failed = false;
    const response = await request(
      `${sessionPath}/turns`,
      sendJson("POST", { prompt }),
    );
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const block = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        for (const line of block.split("\n")) {
          if (!line.startsWith("data: ")) continue;
          try {
            await handleEvent(JSON.parse(line.slice(6)));
          } catch (error) {
            failed = true;
            process.stdout.write(color(theme.error, `\n${error.message}\n`));
          }
        }
      }
    }
    process.stdout.write("\n\n");
    return !failed;
  };

  const help = () => {
    console.log(`${color(theme.accent, "Friday Agent commands")}
  /help                 show commands
  /plan                 toggle read-only plan mode
  /rewind [n]            restore the last n prompts and file changes
  /context              show session context and token usage
  /compact              summarize and compact session history
  /theme [name]         set midnight, ember, or forest
  /clear                clear the terminal
  /exit                 close this agent session`);
  };

  const command = async (line) => {
    const [name, ...parts] = line.slice(1).trim().split(/\s+/);
    const arg = parts.join(" ").trim();
    switch (name) {
      case "help":
        help();
        return;
      case "exit":
      case "quit":
        return "exit";
      case "clear":
        process.stdout.write("\x1b[2J\x1b[H");
        return;
      case "plan": {
        mode = mode === "plan" ? "default" : "plan";
        const updated = await json(sessionPath, sendJson("PATCH", { mode }));
        mode = updated.mode;
        console.log(
          color(
            theme.accent,
            `Plan mode ${mode === "plan" ? "ON (read-only)" : "OFF"}`,
          ),
        );
        return;
      }
      case "rewind": {
        const count = Number.parseInt(arg || "1", 10);
        if (!Number.isInteger(count) || count < 1)
          throw new Error("Usage: /rewind [positive count]");
        const result = await json(
          `${sessionPath}/rewind`,
          sendJson("POST", { count }),
        );
        console.log(
          result.rewound.length
            ? `Rewound: ${result.rewound.join(", ")}`
            : "Nothing to rewind.",
        );
        return;
      }
      case "context": {
        const result = await json(`${sessionPath}/context`);
        console.log(
          `messages=${result.messages}  estimated context ~${result.estimated_tokens} tokens  ` +
            `session usage in=${result.input_tokens} out=${result.output_tokens}  undo=${result.undo_count}`,
        );
        return;
      }
      case "compact": {
        const result = await json(
          `${sessionPath}/compact`,
          sendJson("POST", {}),
        );
        console.log(`History compacted. ${result.messages} messages remain.`);
        return;
      }
      case "theme": {
        if (!arg) {
          console.log(`Themes (dark only): ${Object.keys(THEMES).join(", ")}`);
          return;
        }
        if (!Object.hasOwn(THEMES, arg))
          throw new Error(
            `Unknown theme. Choose: ${Object.keys(THEMES).join(", ")}`,
          );
        themeName = arg;
        theme = THEMES[themeName];
        console.log(`Theme: ${themeName}`);
        return;
      }
      default:
        if (customCommands[name]) {
          return runTurn(customCommands[name].replaceAll("$ARGUMENTS", arg));
        }
        throw new Error(`Unknown command /${name}. Try /help.`);
    }
  };

  const printPrompt = () =>
    process.stdout.write(
      `${color(theme.accent, "🐺 BOSS")} ${mode === "plan" ? color(theme.muted, "[plan] ") : ""}${color(theme.muted, "❯ ")}`,
    );
  const processLine = async (raw) => {
    const line = raw.trim();
    if (!line) return;
    if (line.startsWith("/")) {
      const result = await command(line);
      if (result === "exit") return result;
    } else if (line.length > 4000) {
      throw new Error("Message too long (maximum 4000 characters).");
    } else {
      process.stdout.write(`\n${color(theme.muted, "you ›")} ${line}\n`);
      await runTurn(line);
    }
  };

  const printForLine = async (line) => {
    if (approvalResolver) {
      const resolve = approvalResolver;
      approvalResolver = null;
      resolve(line);
      return;
    }
    queue = queue.then(async () => {
      try {
        if ((await processLine(line)) === "exit") rl.close();
      } catch (error) {
        failed = true;
        process.stdout.write(color(theme.error, `\nError: ${error.message}\n`));
      }
      if (!rl.closed) printPrompt();
    });
  };
  rl.on("line", (line) => void printForLine(line));
  rl.on("close", () => {
    void json(sessionPath, { method: "DELETE" }).catch(() => {});
  });

  const printArg = args.findIndex((arg) => arg === "-p" || arg === "--print");
  if (printArg >= 0) {
    const prompt = args
      .slice(printArg + 1)
      .filter((arg) => !arg.startsWith("-"))
      .join(" ")
      .trim();
    if (!prompt) {
      console.error('usage: friday agent -p "your coding task"');
      rl.close();
      return 1;
    }
    const ok = await runTurn(prompt);
    rl.close();
    return ok ? 0 : 1;
  }

  console.log(
    color(
      theme.muted,
      `\nLocal coding session · ${session.model} · ${session.workspace}`,
    ),
  );
  console.log(
    color(
      theme.muted,
      "Use /help for commands. Network and edits remain permission-gated.\n",
    ),
  );
  printPrompt();
  return new Promise((resolve) =>
    rl.on("close", () => queue.then(() => resolve(0))),
  );
}

module.exports = { start };
