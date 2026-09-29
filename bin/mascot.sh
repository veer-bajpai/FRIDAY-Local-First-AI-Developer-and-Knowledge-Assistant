#!/usr/bin/env bash
set -eu

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
columns="$(tput cols 2>/dev/null || true)"
colors="$(tput colors 2>/dev/null || true)"
export COLUMNS="${columns:-${COLUMNS:-0}}"
export FRIDAY_COLOR_COUNT="${colors:-${FRIDAY_COLOR_COUNT:-0}}"
exec node "$script_dir/mascot.js" "${1:-${FRIDAY_THEME:-gray}}"
