"use strict";
/* eslint-disable @typescript-eslint/no-require-imports */
/**
 * Startup banner for FRIDAY: a pixel-art avatar + big block letters.
 * Auto-skipped when output isn't a terminal, NO_COLOR is set, or FRIDAY_NO_BANNER=1.
 *   FRIDAY_THEME  = cyan (default) | orange | green | purple   (colour of the letters)
 *   FRIDAY_MASCOT = 0                                          (hide the avatar)
 */
const os = require("os");
const path = require("path");

const LOGO = [
  "███████╗██████╗ ██╗██████╗  █████╗ ██╗   ██╗",
  "██╔════╝██╔══██╗██║██╔══██╗██╔══██╗╚██╗ ██╔╝",
  "█████╗  ██████╔╝██║██║  ██║███████║ ╚████╔╝ ",
  "██╔══╝  ██╔══██╗██║██║  ██║██╔══██║  ╚██╔╝  ",
  "██║     ██║  ██║██║██████╔╝██║  ██║   ██║   ",
  "╚═╝     ╚═╝  ╚═╝╚═╝╚═════╝ ╚═╝  ╚═╝   ╚═╝   ",
];
const TAGLINE = "Fast Retrieval, Intelligent Dialogue & Autonomous Yield";

// Pixel-art avatar of FRIDAY (woman with an AI headset). Each terminal line shows 2 pixel rows (half-blocks).
const MASCOT = [
  ".......OOOOOOOOOO.......",
  "......OGGGGggGGGGO......",
  "....OGGHHhHHHHhHHGGO....",
  "...OGHHHHSSSSSSHHHHGO...",
  "...OGHHSSSSSSSSSSHHGO...",
  "...GGGSEESSSSSSEESGGG...",
  "...GgGSWCSSSSSSCWSGgG...",
  "...GgGSSSSSssSSSSSGgG...",
  "...GGGSPPSSSSSSPPSGGG...",
  "...OGGGGGgSRRSSSSSHHO...",
  "....OHHSSSSSSSSSSHHO....",
  ".....OHHSSSSSSSSHHO.....",
  "......OHHHSSSSHHHO......",
  "...OJJJJJJSSSSJJJJJJO...",
  "..OJJJJJJJJJJJJJJJJJJO..",
  "..OOOOOOOOOOOOOOOOOOOO..",
];
const PALETTE = {
  O: [30, 18, 16], // outline
  H: [58, 34, 28], // hair
  h: [120, 74, 54], // hair highlight
  S: [238, 190, 160], // skin
  s: [205, 145, 120], // nose shadow
  E: [25, 15, 12], // lashes / brows
  W: [255, 255, 255], // eye white
  C: [0, 190, 230], // iris
  R: [190, 55, 80], // lips
  P: [230, 140, 135], // blush
  G: [38, 48, 68], // headset
  g: [0, 229, 255], // headset glow
  J: [45, 55, 80], // jacket
};

const THEMES = {
  cyan: { from: [0, 229, 255], to: [41, 121, 255], basic: 36 },
  orange: { from: [240, 150, 110], to: [204, 95, 60], basic: 33 },
  green: { from: [105, 240, 174], to: [0, 200, 120], basic: 32 },
  purple: { from: [206, 147, 255], to: [124, 77, 255], basic: 35 },
};

const RESET = "\x1b[0m";
const truecolor = () =>
  /truecolor|24bit/i.test(process.env.COLORTERM || "") ||
  !!process.env.WT_SESSION ||
  process.env.TERM_PROGRAM === "vscode" ||
  process.env.TERM_PROGRAM === "iTerm.app";

const lerp = (a, b, t) => Math.round(a + (b - a) * t);
const to256 = ([r, g, b]) =>
  16 + 36 * Math.round(r / 51) + 6 * Math.round(g / 51) + Math.round(b / 51);

function mascotLines(tc) {
  const fg = (c) =>
    tc ? `\x1b[38;2;${c.join(";")}m` : `\x1b[38;5;${to256(c)}m`;
  const bg = (c) =>
    tc ? `\x1b[48;2;${c.join(";")}m` : `\x1b[48;5;${to256(c)}m`;
  const lines = [];
  for (let r = 0; r < MASCOT.length; r += 2) {
    let s = "";
    for (let c = 0; c < MASCOT[0].length; c++) {
      const top = PALETTE[MASCOT[r][c]],
        bot = PALETTE[MASCOT[r + 1][c]];
      if (!top && !bot) s += " ";
      else if (top && !bot) s += fg(top) + "▀" + RESET;
      else if (!top && bot) s += fg(bot) + "▄" + RESET;
      else s += fg(top) + bg(bot) + "▀" + RESET;
    }
    lines.push(s);
  }
  return lines;
}

function printBanner({ version = "", mode = "" } = {}) {
  if (
    !process.stdout.isTTY ||
    process.env.NO_COLOR ||
    process.env.FRIDAY_NO_BANNER
  )
    return;

  const theme =
    THEMES[(process.env.FRIDAY_THEME || "cyan").toLowerCase()] || THEMES.cyan;
  const cols = process.stdout.columns || 80;
  const dim = "\x1b[90m",
    bold = "\x1b[1m";
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
  const tagline = dim + TAGLINE + RESET;
  const meta =
    accent +
    "v" +
    version +
    RESET +
    dim +
    (mode ? "  ·  " + mode : "") +
    "  ·  " +
    cwd +
    RESET;

  const mascotW = MASCOT[0].length;
  const wantMascot = process.env.FRIDAY_MASCOT !== "0";
  const out = [""];

  if (wantMascot && cols >= 2 + mascotW + 2 + LOGO[0].length) {
    // avatar on the left, letters + tagline on the right
    const mascot = mascotLines(tc);
    const right = [
      ...LOGO.map((l, i) => letter(i) + l + RESET),
      "",
      tagline,
      meta,
    ];
    const offset = Math.max(0, right.length - mascot.length); // sit the avatar on the "ground"
    right.forEach((line, i) => {
      const m = mascot[i - offset];
      out.push(
        "  " + (m !== undefined ? m : " ".repeat(mascotW)) + "  " + line,
      );
    });
  } else if (
    wantMascot &&
    cols >= LOGO[0].length + 4 &&
    (process.stdout.rows || 24) >= 24
  ) {
    // not wide enough for side-by-side: avatar stacked above the letters
    mascotLines(tc).forEach((m) => out.push("  " + m));
    LOGO.forEach((l, i) => out.push("  " + letter(i) + l + RESET));
    out.push("", "  " + tagline, "  " + meta);
  } else if (!wantMascot && cols >= LOGO[0].length + 4) {
    LOGO.forEach((l, i) => out.push("  " + letter(i) + l + RESET));
    out.push("", "  " + tagline, "  " + meta);
  } else {
    out.push(
      "  " + bold + accent + "F R I D A Y" + RESET,
      "",
      "  " + tagline,
      "  " + meta,
    );
  }
  out.push("");
  console.log(out.join("\n"));
}

module.exports = { printBanner };
