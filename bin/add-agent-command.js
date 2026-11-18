/*
 * One-time setup: teach bin/friday.js the `friday agent` command.
 * Run once:   node bin\add-agent-command.js
 * Undo:       node bin\add-agent-command.js --remove
 * A backup is saved as bin/friday.js.bak before the first change.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const file = path.join(__dirname, "friday.js");
const START = "// >>> friday agent command";
const END = "// <<< friday agent command";
const BLOCK = [
  START,
  'if (process.argv[2] === "agent") {',
  '  require("./agent.js").main(process.argv.slice(3));',
  "  return;",
  "}",
  END,
  "",
].join("\n");

let src = fs.readFileSync(file, "utf8");
const eol = src.includes("\r\n") ? "\r\n" : "\n";
const clean = src.replace(new RegExp(`${START}[\\s\\S]*?${END}\\r?\\n?`), "");

if (process.argv.includes("--remove")) {
  fs.writeFileSync(file, clean);
  console.log("Removed the `friday agent` command from bin/friday.js.");
  process.exit(0);
}
if (clean !== src) {
  console.log("`friday agent` is already set up.");
  process.exit(0);
}
if (/^\s*(import|export)\s/m.test(src)) {
  console.error("bin/friday.js is an ES module; this script only handles CommonJS. Nothing changed.");
  process.exit(1);
}

if (!fs.existsSync(file + ".bak")) fs.writeFileSync(file + ".bak", src);
const lines = src.split(/\r?\n/);
let at = 0;
if (lines[0] && lines[0].startsWith("#!")) at = 1;
if (lines[at] && /^\s*["']use strict["'];?\s*$/.test(lines[at])) at += 1;
lines.splice(at, 0, ...BLOCK.split("\n").slice(0, -1));
fs.writeFileSync(file, lines.join(eol));
console.log("Done. `friday agent` now opens the FRIDAY console.");
