/*
 * FRIDAY startup banner — "Modern Classic", orange.
 * Dependency-free. Printed once per process; hidden when output is not a terminal,
 * when NO_COLOR is set, with FRIDAY_NO_BANNER=1, and for one-shot `-p` questions.
 *
 * Works with any way the launcher calls it: banner(), banner.print(), printBanner(opts),
 * renderBanner(opts) ... every call prints the banner (once) and returns "" so a caller
 * that writes the return value prints nothing extra.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const os = require("os");

const LOGO = [
  "███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗",
  "██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝",
  "█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝ ",
  "██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝  ",
  "██║     ██║  ██║██║██████╔╝██║  ██║   ██║   ",
  "╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝   ",
];
// warm amber → deep orange, top to bottom
const SHADES = [[255, 196, 92], [255, 172, 64], [255, 148, 40], [246, 124, 28], [232, 102, 22], [150, 70, 20]];

let printed = false;

function enabled() {
  if (printed) return false;
  if (!process.stdout.isTTY || process.env.NO_COLOR || process.env.TERM === "dumb") return false;
  if (process.env.FRIDAY_NO_BANNER === "1") return false;
  const argv = process.argv.slice(2);
  if (argv.includes("-p") || argv.includes("--prompt") || argv.includes("--print")) return false;
  if (argv[0] === "update") return false;
  return true;
}

function version() {
  try {
    return JSON.parse(fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8")).version || "";
  } catch {
    return "";
  }
}

function render(opts) {
  const o = opts && typeof opts === "object" ? opts : {};
  const fg = (r, g, b) => (s) => `\x1b[38;2;${r};${g};${b}m${s}\x1b[0m`;
  const orange = fg(255, 148, 40);
  const muted = fg(140, 132, 124);
  const faint = fg(88, 82, 76);
  const cols = process.stdout.columns || 80;

  const mode = String(o.mode || process.argv[2] || "").replace(/^-.*/, "") || "browser";
  const label = { chat: "terminal chat", agent: "coding agent", browser: "web app", setup: "setup" }[mode] || mode;
  const v = o.version || version();
  let where = process.cwd();
  const home = os.homedir();
  if (where.toLowerCase().startsWith(home.toLowerCase())) where = "~" + where.slice(home.length);

  const lines = [""];
  if (cols >= 50) {
    LOGO.forEach((row, i) => lines.push("  " + fg(...SHADES[i])(row)));
    lines.push("");
    lines.push("  " + muted("Fast Retrieval, Intelligent Dialogue & Autonomous Yield"));
  } else {
    lines.push("  " + `\x1b[48;2;255;148;40m\x1b[38;2;20;14;8m FRIDAY \x1b[0m`);
  }
  const info = [v ? orange("v" + v) : null, muted(label), faint(where)].filter(Boolean).join(faint("  ·  "));
  lines.push("  " + info);
  lines.push("");
  return lines.join("\n");
}

function show(opts) {
  if (!enabled()) return "";
  printed = true;
  process.stdout.write(render(opts) + "\n");
  return "";
}

// Any property the launcher asks for (print, printBanner, showBanner, hud, ...) is the same
// printer, so this file drops in without knowing the launcher's exact call.
const callable = (fn) =>
  new Proxy(fn, {
    get(target, prop) {
      if (prop === "render" || prop === "renderBanner" || prop === "text") return (o) => (enabled() ? render(o) : "");
      if (prop === "default" || prop === "__esModule") return prop === "__esModule" ? false : show;
      if (prop in target) return target[prop];
      if (typeof prop === "symbol") return undefined;
      return show;
    },
  });

module.exports = callable(show);

if (require.main === module) show();
