#!/usr/bin/env bash
# FRIDAY one-command installer for macOS and Linux.
#   curl -fsSL https://raw.githubusercontent.com/veer-bajpai/FRIDAY-Local-First-AI-Knowledge-Assistant/main/install.sh | bash
# or, from a downloaded copy of the project: ./install.sh
set -euo pipefail

REPO_URL="${FRIDAY_REPO:-https://github.com/veer-bajpai/FRIDAY-Local-First-AI-Knowledge-Assistant.git}"
BRANCH="${FRIDAY_BRANCH:-main}"
HOME_DIR="$HOME/.friday"
BIN_DIR="$HOME/.local/bin"

info() { printf '\033[36m▸\033[0m %s\n' "$*"; }
ok() { printf '\033[32m✔\033[0m %s\n' "$*"; }
warn() { printf '\033[33m!\033[0m %s\n' "$*"; }
die() { printf '\033[31m✖\033[0m %s\n' "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
  if have sudo; then SUDO="sudo"; else warn "sudo not found; system package installation may fail."; fi
fi

node_ok() {
  have node && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ]
}
find_python() {
  for candidate in python3.13 python3.12 python3.11 python3 python; do
    if have "$candidate" && "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

install_macos() {
  if ! have brew; then
    info "Installing Homebrew…"
    NONINTERACTIVE=1 /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    for brew_path in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$brew_path" ]; then eval "$("$brew_path" shellenv)"; fi
    done
  fi
  have git || { info "Installing Git…"; brew install git; }
  node_ok || { info "Installing Node.js…"; brew install node; }
  find_python >/dev/null || { info "Installing Python 3.12…"; brew install python@3.12; }
  have ollama || { info "Installing Ollama…"; brew install ollama; }
}

install_linux() {
  if have apt-get; then
    info "Installing system packages (apt)…"
    $SUDO apt-get update -y
    $SUDO apt-get install -y git curl ca-certificates
    if ! node_ok; then
      info "Installing Node.js 20…"
      curl -fsSL https://deb.nodesource.com/setup_20.x | $SUDO -E bash -
      $SUDO apt-get install -y nodejs
    fi
    if ! find_python >/dev/null; then
      info "Installing Python 3.12…"
      $SUDO apt-get install -y python3.12 python3.12-venv || {
        $SUDO apt-get install -y software-properties-common
        $SUDO add-apt-repository -y ppa:deadsnakes/ppa
        $SUDO apt-get update -y
        $SUDO apt-get install -y python3.12 python3.12-venv
      }
    fi
    python_cmd="$(find_python)" || die "Python 3.11+ could not be installed."
    "$python_cmd" -c 'import ensurepip, venv' 2>/dev/null || $SUDO apt-get install -y python3-venv python3.12-venv
  elif have dnf; then
    info "Installing system packages (dnf)…"
    $SUDO dnf install -y git curl
    if ! node_ok; then $SUDO dnf install -y nodejs npm; fi
    if ! find_python >/dev/null; then $SUDO dnf install -y python3.12 || $SUDO dnf install -y python3; fi
  elif have pacman; then
    info "Installing system packages (pacman)…"
    $SUDO pacman -Sy --noconfirm git curl nodejs npm python
  else
    die "Unsupported Linux distro. Install Git, Node.js 20+, Python 3.11+, and Ollama manually, then re-run."
  fi
  have ollama || { info "Installing Ollama…"; curl -fsSL https://ollama.com/install.sh | sh; }
}

case "$(uname -s)" in
  Darwin) install_macos ;;
  Linux) install_linux ;;
  *) die "Unsupported OS. On Windows use install.ps1." ;;
esac

node_ok || die "Node.js 20+ was not found after installation."
find_python >/dev/null || die "Python 3.11+ was not found after installation."
have git || die "Git was not found after installation."
have ollama || warn "Ollama is not on PATH; chat needs Ollama installed from https://ollama.com."

SCRIPT_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
if [ -n "$SCRIPT_DIR" ] && [ -f "$SCRIPT_DIR/bin/friday.js" ]; then
  APP_DIR="$SCRIPT_DIR"
  info "Using project at $APP_DIR"
else
  APP_DIR="$HOME_DIR/app"
  mkdir -p "$HOME_DIR"
  if [ -d "$APP_DIR/.git" ]; then
    info "Updating FRIDAY…"
    git -C "$APP_DIR" pull --ff-only
  else
    info "Downloading FRIDAY…"
    git clone --depth 1 --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
  fi
fi

mkdir -p "$BIN_DIR"
chmod +x "$APP_DIR/bin/friday.js"
ln -sf "$APP_DIR/bin/friday.js" "$BIN_DIR/friday"
export PATH="$BIN_DIR:$PATH"

PATH_MARKER="# added by FRIDAY installer"
for profile in "$HOME/.zshrc" "$HOME/.bashrc"; do
  if [ -f "$profile" ] || { [ "$profile" = "$HOME/.zshrc" ] && [ "$(uname -s)" = "Darwin" ]; }; then
    grep -qF "$PATH_MARKER" "$profile" 2>/dev/null || printf '\n%s\nexport PATH="$HOME/.local/bin:$PATH"\n' "$PATH_MARKER" >> "$profile"
  fi
done

info "Setting up FRIDAY (dependencies, build, and AI models)…"
"$BIN_DIR/friday" setup

echo
ok "FRIDAY is installed. Open a new terminal and run:"
echo "  friday browser    run in your browser"
echo "  friday chat       run in this terminal"
echo "  friday            choose a mode"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) echo "If 'friday' is not found, add to your shell profile: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
