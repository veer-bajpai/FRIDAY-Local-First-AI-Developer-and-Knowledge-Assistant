"use strict";
/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Startup banner for FRIDAY: a dark-only ASCII mascot + big block letters.
 * Auto-skipped when output isn't a terminal, NO_COLOR is set, or FRIDAY_NO_BANNER=1.
 *   FRIDAY_THEME  = gray (default) | blue | green | red
 *   FRIDAY_MASCOT = 0                           (hide the mascot)
 */
const os = require("os");
const path = require("path");
const { renderMascot } = require("./mascot");

const LOGO = [
  "███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗",
  "██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝",
  "█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝ ",
  "██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝  ",
  "██║     ██║  ██║██║██████╔╝██║  ██║   ██║   ",
  "╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝   ",
];
const TAGLINE = "Fast Retrieval, Intelligent Dialogue & Autonomous Yield";

function printBanner({ version = "", mode = "" } = {}) {
  if (
    !process.stdout.isTTY ||
    process.env.NO_COLOR ||
    process.env.FRIDAY_NO_BANNER
  )
    return;

  const cols = process.stdout.columns || 0;
  const cwd = path.normalize(process.cwd().replace(os.homedir(), "~"));
  const meta = `v${version}${mode ? "  ·  " + mode : ""}  ·  ${cwd}`;
  const wantMascot = process.env.FRIDAY_MASCOT !== "0";
  const out = [""];

  if (wantMascot && cols >= 80) {
    const colors = process.stdout.getColorDepth?.() >= 8 ? 256 : 0;
    out.push(
      renderMascot({ theme: process.env.FRIDAY_THEME, columns: cols, colors }),
    );
    out.push(...LOGO, "", TAGLINE, meta);
  } else if (!wantMascot && cols >= LOGO[0].length + 4) {
    out.push(...LOGO, "", TAGLINE, meta);
  } else {
    out.push("FRIDAY");
  }
  out.push("");
  console.log(out.join("\n"));
}

module.exports = { printBanner };
