"use strict";
/* eslint-disable @typescript-eslint/no-require-imports */

const fs = require("fs");
const path = require("path");

const RAMPS = {
  gray: [233, 235, 237, 239, 240, 241, 242, 243, 244],
  blue: [233, 17, 17, 18, 18, 19, 19, 25, 25],
  green: [233, 22, 22, 22, 28, 28, 28, 34, 34],
  red: [233, 52, 52, 88, 88, 88, 124, 124, 124],
};
const LEVELS = new Map(
  [..."@.:-=+*#%"].map((character, index) => [character, index]),
);
const RESET = "\x1b[0m";

function terminalColorCount() {
  if (process.env.NO_COLOR !== undefined) return 0;
  if (process.env.FRIDAY_COLOR_COUNT !== undefined) {
    return Number.parseInt(process.env.FRIDAY_COLOR_COUNT, 10) || 0;
  }
  return process.stdout.isTTY && process.stdout.getColorDepth
    ? process.stdout.getColorDepth() >= 8
      ? 256
      : 0
    : 0;
}

function renderMascot({
  theme = process.env.FRIDAY_THEME || "gray",
  columns = Number.parseInt(process.env.COLUMNS || "", 10) ||
    process.stdout.columns ||
    0,
  colors = terminalColorCount(),
} = {}) {
  if (columns < 80) return "FRIDAY";

  const art = fs
    .readFileSync(path.join(__dirname, "ascii-art.txt"), "utf8")
    .replace(/\r?\n$/, "");
  if (colors < 256 || process.env.NO_COLOR !== undefined) return art;

  const ramp = RAMPS[String(theme).toLowerCase()] || RAMPS.gray;
  return art
    .split("\n")
    .map(
      (line) =>
        [...line]
          .map((character) => {
            const level = LEVELS.get(character);
            return level === undefined
              ? character
              : `\x1b[38;5;${ramp[level]}m${character}`;
          })
          .join("") + RESET,
    )
    .join("\n");
}

if (require.main === module) {
  process.stdout.write(renderMascot({ theme: process.argv[2] }) + "\n");
}

module.exports = { renderMascot };
