#!/usr/bin/env node
/**
 * friday — launcher for FRIDAY.
 *   friday          set up anything missing (deps, Ollama models), then start
 *   friday setup    only do the setup (used by the installers)
 *   friday update   pull latest code
 * App code and behaviour are untouched.
 */
"use strict";

/* eslint-disable @typescript-eslint/no-require-imports */
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");
const http = require("http");
const net = require("net");
const crypto = require("crypto");
const readline = require("readline");

const APP_DIR = path.resolve(__dirname, "..");
const HOME_DIR = path.join(os.homedir(), ".friday");
const VENV_DIR = path.join(HOME_DIR, "venv");
const STATE_FILE = path.join(HOME_DIR, "state.json");
const IS_WIN = process.platform === "win32";
const FRONTEND_PORT = 3000;
const BACKEND_PORT = 8000;
const OLLAMA_URL = process.env.OLLAMA_URL || "http://localhost:11434";
const CHAT_MODEL = process.env.FRIDAY_MODEL || "llama3.2";
const EMBED_MODEL = process.env.FRIDAY_EMBED_MODEL || "nomic-embed-text";
const VERSION = require(path.join(APP_DIR, "package.json")).version;

const c = (n, s) => (process.stdout.isTTY ? `\x1b[${n}m${s}\x1b[0m` : s);
const info = (m) => console.log(c(36, "▸ ") + m);
const ok = (m) => console.log(c(32, "✔ ") + m);
const warn = (m) => console.log(c(33, "! ") + m);
const die = (m) => {
  console.error(c(31, "✖ ") + m);
  process.exit(1);
};

const args = process.argv.slice(2);
const cmd = args.find((a) => !a.startsWith("-"));

if (args.includes("-h") || args.includes("--help")) {
  console.log(`FRIDAY ${VERSION}

  friday              Choose browser or terminal chat (defaults to browser without a TTY)
  friday browser      Start FRIDAY and open it in your browser
  friday --no-open    Start browser mode without opening a browser tab
  friday chat         Chat with FRIDAY right in the terminal (no browser)
  friday chat -p "…"  Ask one question, print the answer, exit
  friday setup        Install dependencies, build, and download Ollama models
  friday update       Pull the latest version (rebuilds on next start)
  friday --version    Print version

Set FRIDAY_THEME=gray|blue|green|red to change the dark mascot palette; FRIDAY_NO_BANNER=1 hides the banner.
Terminal chat shares history with the web UI. Data: ~/.friday/data (override with FRIDAY_DATA_DIR)`);
  process.exit(0);
}
if (args.includes("-v") || args.includes("--version")) {
  console.log(VERSION);
  process.exit(0);
}

const KNOWN_COMMANDS = ["browser", "web", "start", "chat", "setup", "update"];
if (cmd && !KNOWN_COMMANDS.includes(cmd)) {
  die(
    `Unknown command "${cmd}". Try: friday browser | friday chat | friday --help`,
  );
}

// ---------- helpers ----------
function run(command, cmdArgs, opts = {}) {
  const r = spawnSync(command, cmdArgs, {
    stdio: "inherit",
    cwd: APP_DIR,
    shell: IS_WIN && opts.shell !== false,
    ...opts,
  });
  return r.status === 0;
}
function has(command, versionArg = "--version") {
  const r = spawnSync(command, [versionArg], {
    encoding: "utf8",
  });
  return r.status === 0 ? (r.stdout || r.stderr).trim() : null;
}
function readState() {
  try {
    return JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  } catch {
    return {};
  }
}
function writeState(s) {
  fs.mkdirSync(HOME_DIR, { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}
function fileHash(p) {
  return fs.existsSync(p)
    ? crypto.createHash("sha1").update(fs.readFileSync(p)).digest("hex")
    : "";
}
function gitHead() {
  const r = spawnSync("git", ["rev-parse", "HEAD"], {
    cwd: APP_DIR,
    encoding: "utf8",
  });
  return r.status === 0 ? r.stdout.trim() : VERSION;
}
function portInUse(port) {
  return new Promise((res) => {
    const s = net
      .createServer()
      .once("error", () => res(true))
      .once("listening", () => s.close(() => res(false)));
    s.listen(port, "127.0.0.1");
  });
}
function waitFor(url, tries = 90) {
  return new Promise((resolve) => {
    const retry = (n) => {
      if (n <= 0) return resolve(false);
      setTimeout(() => attempt(n - 1), 1000);
    };
    const attempt = (n) => {
      const request = http
        .get(url, (r) => {
          if (r.statusCode < 200 || r.statusCode >= 300) {
            r.resume();
            return retry(n);
          }
          r.resume();
          resolve(true);
        })
        .on("error", () => retry(n));
      request.setTimeout(3000, () => request.destroy());
    };
    attempt(tries);
  });
}
function openBrowser(url) {
  const o = IS_WIN
    ? ["cmd", ["/c", "start", "", url]]
    : process.platform === "darwin"
      ? ["open", [url]]
      : ["xdg-open", [url]];
  try {
    spawn(o[0], o[1], { stdio: "ignore", detached: true })
      .on("error", () => {})
      .unref();
  } catch {}
}

// ---------- update ----------
if (cmd === "update") {
  if (!fs.existsSync(path.join(APP_DIR, ".git")))
    die("Not a git checkout. Re-run the install script to update.");
  info("Updating FRIDAY…");
  if (!run("git", ["pull", "--ff-only"])) die("git pull failed.");
  const st = readState();
  delete st.buildStamp;
  delete st.pipStamp;
  writeState(st);
  ok("Updated. Changes will be built on next `friday` start.");
  process.exit(0);
}

// ---------- prerequisites ----------
if (parseInt(process.versions.node, 10) < 20)
  die(`Node.js 20+ required (found ${process.versions.node}).`);
let PY = null;
for (const cand of IS_WIN
  ? ["py", "python", "python3"]
  : ["python3.13", "python3.12", "python3.11", "python3", "python"]) {
  const v = has(cand);
  if (v && /Python 3\.(1[1-9]|[2-9]\d)/.test(v)) {
    PY = cand;
    break;
  }
}
if (!PY)
  die(
    "Python 3.11+ not found. Re-run the FRIDAY install script, or install Python from python.org.",
  );

const venvPy = path.join(
  VENV_DIR,
  IS_WIN ? "Scripts" : "bin",
  IS_WIN ? "python.exe" : "python",
);
const reqFile = path.join(APP_DIR, "backend", "requirements.txt");

// ---------- one-time setup (cached) ----------
function setupApp({ skipBuild = false } = {}) {
  const state = readState();
  if (!fs.existsSync(path.join(APP_DIR, "node_modules", "next"))) {
    info("Installing frontend dependencies…");
    if (!run("npm", ["install", "--no-audit", "--no-fund"]))
      die("npm install failed.");
  }
  if (!fs.existsSync(venvPy)) {
    info("Creating Python environment…");
    fs.mkdirSync(HOME_DIR, { recursive: true });
    if (
      !run(
        PY,
        PY === "py" ? ["-3", "-m", "venv", VENV_DIR] : ["-m", "venv", VENV_DIR],
      )
    )
      die("Could not create Python virtual environment.");
    delete state.pipStamp;
  }
  const reqHash = fileHash(reqFile);
  if (state.pipStamp !== reqHash) {
    info("Installing backend dependencies…");
    if (
      !run(venvPy, ["-m", "pip", "install", "-q", "-r", reqFile], {
        shell: false,
      })
    )
      die("pip install failed.");
    state.pipStamp = reqHash;
    writeState(state);
  }
  if (skipBuild) return;
  const stamp =
    gitHead() + ":" + fileHash(path.join(APP_DIR, "package-lock.json"));
  if (
    state.buildStamp !== stamp ||
    !fs.existsSync(path.join(APP_DIR, ".next"))
  ) {
    info("Building frontend…");
    if (!run("npm", ["run", "build"])) die("Frontend build failed.");
    state.buildStamp = stamp;
    writeState(state);
  }
}

// ---------- Ollama: start server if needed, download models if missing ----------
let ollamaChild = null;
async function ensureOllama() {
  if (!has("ollama")) {
    warn(
      "Ollama is not installed — chat/embeddings won't work. Re-run the install script or get it from https://ollama.com",
    );
    return false;
  }
  if (!(await waitFor(OLLAMA_URL, 1))) {
    info("Starting Ollama…");
    ollamaChild = spawn("ollama", ["serve"], {
      stdio: "ignore",
    });
    ollamaChild.on("error", () => {});
    if (!(await waitFor(OLLAMA_URL, 30))) {
      warn("Ollama did not start; continuing without it.");
      return false;
    }
  }
  const list = (
    spawnSync("ollama", ["list"], { encoding: "utf8" }).stdout || ""
  ).toLowerCase();
  for (const model of [EMBED_MODEL, CHAT_MODEL]) {
    const base = model.split(":")[0].toLowerCase();
    if (!list.includes(base)) {
      info(`Downloading model ${model} (one-time, may take a few minutes)…`);
      if (!run("ollama", ["pull", model]))
        warn(`Could not download ${model}. Run: ollama pull ${model}`);
    }
  }
  return true;
}

// ---------- shared env ----------
function buildEnv() {
  const env = {
    ...process.env,
    NEXT_PUBLIC_API_URL:
      process.env.NEXT_PUBLIC_API_URL || `http://localhost:${BACKEND_PORT}`,
    FRIDAY_DATA_DIR: process.env.FRIDAY_DATA_DIR || path.join(HOME_DIR, "data"),
    PYTHONPATH: [APP_DIR, process.env.PYTHONPATH]
      .filter(Boolean)
      .join(path.delimiter),
    PYTHONUNBUFFERED: "1",
  };
  fs.mkdirSync(env.FRIDAY_DATA_DIR, { recursive: true });
  return env;
}
const killTree = (ch) => {
  try {
    if (IS_WIN) spawnSync("taskkill", ["/pid", String(ch.pid), "/T", "/F"]);
    else ch.kill("SIGTERM");
  } catch {}
};

// ---------- terminal chat: reuse a running backend, or start one quietly ----------
async function runChat() {
  const api = `http://localhost:${BACKEND_PORT}`;
  let backend = null;
  if (!(await waitFor(api + "/health", 1))) {
    info("Starting FRIDAY backend…");
    fs.mkdirSync(HOME_DIR, { recursive: true });
    const log = fs.openSync(path.join(HOME_DIR, "backend.log"), "a");
    backend = spawn(
      venvPy,
      [
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        String(BACKEND_PORT),
      ],
      {
        cwd: path.join(APP_DIR, "backend"),
        env: buildEnv(),
        stdio: ["ignore", log, log],
      },
    );
    backend.on("error", () => {});
    if (!(await waitFor(api + "/health", 60))) {
      killTree(backend);
      die(`Backend did not start. Check ${path.join(HOME_DIR, "backend.log")}`);
    }
  }
  const code = await require("./chat.js").start({ api, args });
  if (backend) killTree(backend);
  if (ollamaChild) killTree(ollamaChild);
  process.exit(code || 0);
}

async function chooseMode() {
  if (!process.stdin.isTTY || !process.stdout.isTTY) return "browser";

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });
  console.log(`  How do you want to use FRIDAY?

    ${c(36, "1")}  Browser         web app at http://localhost:${FRONTEND_PORT}      ${c(90, "(friday browser)")}
    ${c(36, "2")}  Terminal chat   chat right here, no browser       ${c(90, "(friday chat)")}
`);

  return new Promise((resolve) => {
    const prompt = () => process.stdout.write("  Choose 1 or 2: ");
    rl.on("SIGINT", () => {
      rl.close();
      console.log();
      process.exit(0);
    });
    rl.on("line", (line) => {
      const choice = line.trim().toLowerCase();
      if (["1", "b", "browser", "web"].includes(choice)) {
        rl.close();
        console.log();
        resolve("browser");
      } else if (["2", "c", "chat", "t", "terminal"].includes(choice)) {
        rl.close();
        console.log();
        resolve("chat");
      } else {
        prompt();
      }
    });
    prompt();
  });
}

// ---------- main ----------
(async () => {
  const oneShot =
    cmd === "chat" && (args.includes("-p") || args.includes("--print"));
  if (!oneShot && cmd !== "update") {
    require("./banner.js").printBanner({
      version: VERSION,
      mode:
        cmd === "chat"
          ? "terminal chat"
          : cmd === "setup"
            ? "setup"
            : cmd
              ? "browser"
              : "",
    });
  }
  let mode = cmd === "web" || cmd === "start" ? "browser" : cmd;
  if (!mode) mode = args.includes("--no-open") ? "browser" : await chooseMode();
  setupApp({ skipBuild: mode === "chat" });
  await ensureOllama();
  if (mode === "chat") return runChat();
  if (mode === "setup") {
    ok(
      "Setup complete. Start FRIDAY with:  friday browser   (or terminal chat:  friday chat)",
    );
    if (ollamaChild) ollamaChild.kill();
    return process.exit(0);
  }

  for (const p of [FRONTEND_PORT, BACKEND_PORT]) {
    if (await portInUse(p))
      die(`Port ${p} is already in use. Is FRIDAY already running?`);
  }
  const env = buildEnv();

  const children = [];
  let shuttingDown = false;
  const shutdown = (code = 0) => {
    if (shuttingDown) return;
    shuttingDown = true;
    children.forEach(killTree);
    if (ollamaChild) killTree(ollamaChild);
    setTimeout(() => process.exit(code), 300);
  };
  const start = (name, command, cmdArgs, cwd) => {
    const ch = spawn(command, cmdArgs, {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
    });
    const tag = c(90, `[${name}] `);
    const pipe = (s, out) =>
      s.on("data", (d) =>
        out.write(
          String(d)
            .split("\n")
            .filter(Boolean)
            .map((l) => tag + l)
            .join("\n") + "\n",
        ),
      );
    pipe(ch.stdout, process.stdout);
    pipe(ch.stderr, process.stderr);
    ch.on("exit", (code) => {
      if (!shuttingDown) {
        warn(`${name} stopped (code ${code}).`);
        shutdown(code || 1);
      }
    });
    children.push(ch);
  };
  process.on("SIGINT", () => shutdown(0));
  process.on("SIGTERM", () => shutdown(0));

  info("Starting FRIDAY…");
  start(
    "backend",
    venvPy,
    [
      "-m",
      "uvicorn",
      "app.main:app",
      "--host",
      "127.0.0.1",
      "--port",
      String(BACKEND_PORT),
    ],
    path.join(APP_DIR, "backend"),
  );
  start(
    "frontend",
    process.execPath,
    [
      path.join(APP_DIR, "node_modules", "next", "dist", "bin", "next"),
      "start",
      "-p",
      String(FRONTEND_PORT),
    ],
    APP_DIR,
  );

  const url = `http://localhost:${FRONTEND_PORT}`;
  if (await waitFor(url)) {
    ok(`FRIDAY is running at ${c(1, url)}  (Ctrl+C to stop)`);
    if (!args.includes("--no-open")) openBrowser(url);
  } else warn("Frontend did not respond in time; check the logs above.");
})();
