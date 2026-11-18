/*
 * `friday agent` — open the FRIDAY operator console (friday_agent/terminal.py) in one command.
 *
 * 1. Uses the backend if it is already running; otherwise starts it with this
 *    launcher (`friday --no-open`) and stops it again when the console closes.
 * 2. Finds a Python that has Textual installed and runs `python -m friday_agent.terminal`.
 *
 * Env: FRIDAY_API_URL (default http://127.0.0.1:8000), FRIDAY_PYTHON (python to use).
 */
"use strict";

const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const os = require("os");
const path = require("path");

const APP = path.resolve(__dirname, "..");
const API = (process.env.FRIDAY_API_URL || "http://127.0.0.1:8000").replace(/\/+$/, "");
const WIN = process.platform === "win32";
const orange = (s) => (process.stdout.isTTY && !process.env.NO_COLOR ? `\x1b[38;2;255;148;40m${s}\x1b[0m` : s);
const muted = (s) => (process.stdout.isTTY && !process.env.NO_COLOR ? `\x1b[38;2;140;132;124m${s}\x1b[0m` : s);
const say = (s) => process.stdout.write(`  ${orange("▌")} ${muted(s)}\n`);

async function backendUp() {
  try {
    const res = await fetch(API + "/health", { signal: AbortSignal.timeout(1500) });
    return res.ok;
  } catch {
    return false;
  }
}

function pythonCandidates() {
  const bin = WIN ? ["Scripts", "python.exe"] : ["bin", "python"];
  return [
    process.env.FRIDAY_PYTHON,
    path.join(os.homedir(), ".friday", "venv", ...bin),
    path.join(APP, ".venv", ...bin),
    path.join(APP, "backend", ".venv", ...bin),
    WIN ? "python" : "python3",
    "python",
  ].filter(Boolean);
}

function findPython() {
  for (const py of pythonCandidates()) {
    if (path.isAbsolute(py) && !fs.existsSync(py)) continue;
    const r = spawnSync(py, ["-c", "import textual, rich, httpx"], { stdio: "ignore", timeout: 20000 });
    if (r.status === 0) return py;
  }
  return null;
}

function stop(child) {
  if (!child || child.exitCode !== null) return;
  if (WIN) spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  else {
    try {
      process.kill(-child.pid, "SIGTERM");
    } catch {
      child.kill("SIGTERM");
    }
  }
}

async function main(args = []) {
  const python = findPython();
  if (!python) {
    say("The console needs Textual. Install it with:");
    process.stdout.write(`\n    ${path.join(".venv", WIN ? "Scripts\\pip" : "bin/pip")} install textual rich httpx\n\n`);
    process.exitCode = 1;
    return;
  }

  let backend = null;
  if (!(await backendUp())) {
    say("starting the FRIDAY backend…");
    backend = spawn(process.execPath, [path.join(__dirname, "friday.js"), "--no-open"], {
      cwd: APP,
      stdio: "ignore",
      detached: !WIN,
      windowsHide: true,
      env: { ...process.env, FRIDAY_NO_BANNER: "1" },
    });
    backend.on("error", () => {});
    const deadline = Date.now() + 90_000;
    while (!(await backendUp())) {
      if (backend.exitCode !== null || Date.now() > deadline) {
        stop(backend);
        say("The backend didn't start. Run `friday` in another window, then `friday agent` again.");
        process.exitCode = 1;
        return;
      }
      await new Promise((r) => setTimeout(r, 700));
    }
  }

  const env = { ...process.env, FRIDAY_API_URL: API };
  env.PYTHONPATH = [APP, process.env.PYTHONPATH].filter(Boolean).join(path.delimiter);
  const consoleApp = spawn(python, ["-m", "friday_agent.terminal", ...args], { stdio: "inherit", env });
  const cleanup = () => stop(backend);
  process.on("SIGINT", () => {}); // Ctrl+C belongs to the console, not this launcher
  consoleApp.on("error", (e) => {
    say(`Couldn't open the console: ${e.message}`);
    cleanup();
    process.exitCode = 1;
  });
  consoleApp.on("exit", (code) => {
    cleanup();
    process.exit(code || 0);
  });
}

module.exports = { main };

if (require.main === module) main(process.argv.slice(2));
