"use strict";
/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Startup banner for FRIDAY: muted dark-theme block letters and status text.
 * Auto-skipped when output isn't a terminal, NO_COLOR is set, or FRIDAY_NO_BANNER=1.
 *   FRIDAY_THEME  = midnight (default) | ember | forest
 */
const os = require("os");
const path = require("path");
const { spawnSync } = require("child_process");

const LOGO = [
  "███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗",
  "██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝",
  "█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝ ",
  "██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝  ",
  "██║     ██║  ██║██║██████╔╝██║  ██║   ██║   ",
  "╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝   ",
];
const THEMES = {
  midnight: { from: [108, 122, 140], to: [77, 89, 105], basic: 90 },
  ember: { from: [151, 112, 99], to: [105, 75, 69], basic: 31 },
  forest: { from: [111, 132, 112], to: [70, 91, 76], basic: 32 },
};

const RESET = "\x1b[0m";
const HUD = {
  cyan: "\x1b[38;5;67m",
  orange: "\x1b[38;5;137m",
  red: "\x1b[38;5;131m",
  white: "\x1b[38;5;252m",
  gray: "\x1b[38;5;240m",
  bold: "\x1b[1m",
};
const truecolor = () =>
  /truecolor|24bit/i.test(process.env.COLORTERM || "") ||
  !!process.env.WT_SESSION ||
  process.env.TERM_PROGRAM === "vscode" ||
  process.env.TERM_PROGRAM === "iTerm.app";

const lerp = (a, b, t) => Math.round(a + (b - a) * t);

function visibleLength(value) {
  const plain = value.replace(/\x1b\[[0-?]*[ -/]*[@-~]/g, "");
  return [...plain].reduce((length, character) => {
    if (character === "\uFE0F") return length;
    const codePoint = character.codePointAt(0);
    const isWideEmoji =
      (codePoint >= 0x1f300 && codePoint <= 0x1faff) ||
      (codePoint >= 0x2600 && codePoint <= 0x27bf);
    return length + (isWideEmoji ? 2 : 1);
  }, 0);
}

function boxedLine(value, width) {
  const padding = " ".repeat(Math.max(0, width - visibleLength(value)));
  return `${HUD.gray}│${RESET}${value}${padding}${HUD.gray}│${RESET}`;
}

function batteryInfo() {
  const run = (command, args) =>
    spawnSync(command, args, {
      encoding: "utf8",
      timeout: 1500,
      windowsHide: true,
      stdio: ["ignore", "pipe", "ignore"],
    });

  if (process.platform === "win32") {
    const result = run("powershell.exe", [
      "-NoProfile",
      "-NonInteractive",
      "-Command",
      "(Get-CimInstance Win32_Battery -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty EstimatedChargeRemaining)",
    ]);
    const percentage = Number.parseInt((result.stdout || "").trim(), 10);
    return Number.isInteger(percentage) ? `${percentage}%` : "N/A";
  }

  if (process.platform === "darwin") {
    const result = run("pmset", ["-g", "batt"]);
    return result.stdout?.match(/\d+%/)?.[0] || "N/A";
  }

  const devices = run("upower", ["-e"]);
  const battery = (devices.stdout || "")
    .split(/\r?\n/)
    .find((device) => /battery/i.test(device));
  if (!battery) return "N/A";
  const result = run("upower", ["-i", battery]);
  return result.stdout?.match(/percentage:\s*(\S+)/)?.[1] || "N/A";
}

function gitBranch() {
  const result = spawnSync("git", ["branch", "--show-current"], {
    cwd: process.cwd(),
    encoding: "utf8",
    timeout: 1000,
    windowsHide: true,
    stdio: ["ignore", "pipe", "ignore"],
  });
  const branch = result.status === 0 ? result.stdout.trim() : "";
  return branch
    ? ` 🌿 ${HUD.orange}git:(${HUD.white}${branch.slice(0, 20)}${HUD.orange})${RESET}`
    : "";
}

function greeting(hour) {
  if (hour < 12) return "Good morning, Boss.";
  if (hour < 17) return "Good afternoon, Boss.";
  return "Good evening, Boss.";
}

function printBanner({ version = "", mode = "" } = {}) {
  if (
    !process.stdout.isTTY ||
    process.env.NO_COLOR ||
    process.env.FRIDAY_NO_BANNER
  )
    return;

  const theme =
    THEMES[(process.env.FRIDAY_THEME || "midnight").toLowerCase()] ||
    THEMES.midnight;
  const cols = process.stdout.columns || 80;
  const tc = truecolor();
  const letter = (i) => {
    if (!tc) return `\x1b[${theme.basic}m`;
    const t = i / (LOGO.length - 1);
    const [r, g, b] = theme.from.map((v, k) => lerp(v, theme.to[k], t));
    return `\x1b[38;2;${r};${g};${b}m`;
  };
  const accent = tc
    ? `\x1b[38;2;${theme.from.join(";")}m`
    : `\x1b[${theme.basic}m`;

  const cwd = path.normalize(process.cwd().replace(os.homedir(), "~"));
  const now = new Date();
  const time = now.toLocaleTimeString("en-US", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
  const exitCode = Number.isInteger(process.exitCode) ? process.exitCode : 0;
  const core = `${exitCode === 0 ? HUD.cyan : HUD.red}🐺 BOSS${RESET}`;
  const status =
    exitCode === 0
      ? `${HUD.cyan}ONLINE // SYSTEMS NOMINAL${RESET}`
      : `${HUD.red}ALERT // CODE_ERROR: ${exitCode}${RESET}`;
  const telemetry = `  Telemetry: 🕒 [${HUD.white}${time}${RESET}]  🔋 [${HUD.white}${batteryInfo()}${RESET}]${gitBranch()}`;
  const context = `  Context  : ${mode ? mode + "  ·  " : ""}${cwd}`;

  const out = [""];

  if (cols >= LOGO[0].length + 4) {
    const width = Math.min(76, cols - 2);
    const title = `───[ ${HUD.white}F.R.I.D.A.Y. OS v${version}${HUD.gray} ]`;
    out.push(
      `${HUD.gray}┌${title}${"─".repeat(Math.max(0, width - visibleLength(title)))}┐${RESET}`,
    );
    LOGO.forEach((line, index) =>
      out.push(boxedLine("  " + letter(index) + line + RESET, width)),
    );
    out.push(
      boxedLine(
        `  ${core}  ${HUD.bold}${HUD.white}${greeting(now.getHours())}${RESET}`,
        width,
      ),
      boxedLine(telemetry, width),
      boxedLine(`  Status   : ${status}`, width),
      boxedLine(context, width),
      `${HUD.gray}└${"─".repeat(width)}┘${RESET}`,
    );
  } else {
    out.push(
      "  " + HUD.bold + accent + "F R I D A Y" + RESET,
      `  ${core}  ${HUD.white}${greeting(now.getHours())}${RESET}`,
      telemetry,
      `  Status   : ${status}`,
      context,
    );
  }
  out.push("");
  console.log(out.join("\n"));
}

module.exports = { printBanner };
