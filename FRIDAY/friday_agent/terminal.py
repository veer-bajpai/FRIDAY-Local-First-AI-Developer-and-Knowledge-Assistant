"""FRIDAY operator console: full-screen terminal workstation for the local coding agent.

Layout (wide terminals):

    ▌FRIDAY ● LINK  model  workspace  ⎇ branch ±n  mode  net        HH:MM:SS
    ┌ transcript ─────────────────────────────────┐ ┌ side panel ──────────┐
    │ ❯ BOSS     prompt                           │ │ TASK / TODO / TOOLS  │
    │ ├─ ▸ read_file  src/app.py                  │ │ CONTEXT / MODEL      │
    │ │   ✓ 0.2s  120 lines                       │ │ GIT / COMMANDS       │
    │ ◆ FRIDAY   markdown reply with code blocks  │ │                      │
    └─────────────────────────────────────────────┘ └──────────────────────┘
    ⠹ Reading context...  0:04
    ❯ input
    ◆ READY  mode default  net ask          ⏎ send  ^C interrupt  F2 panel ...

The side panel hides itself on narrow terminals. All actions go to the real
FRIDAY agent API; nothing here is simulated.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import httpx
from rich.console import Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import Button, Input, RichLog, Static

# --------------------------------------------------------------------------- palette

THEMES: dict[str, dict[str, str]] = {
    # graphite + restrained phosphor green, cyan / amber secondary
    "midnight": {
        "bg": "#0a0c0d", "surface": "#0e1112", "panel": "#12171a", "rule": "#1e262a",
        "text": "#c8d0cc", "muted": "#5d6a70", "accent": "#5fd49a", "cyan": "#5ec2d6",
        "amber": "#e0b05a", "red": "#e5686b", "code": "monokai",
    },
    "ember": {
        "bg": "#0c0a09", "surface": "#110e0c", "panel": "#171310", "rule": "#2a221d",
        "text": "#d2c8c0", "muted": "#6e625a", "accent": "#e8a05c", "cyan": "#c9b37a",
        "amber": "#f0c060", "red": "#e5686b", "code": "monokai",
    },
    "forest": {
        "bg": "#080c09", "surface": "#0c110d", "panel": "#111812", "rule": "#1c271e",
        "text": "#c5d0c6", "muted": "#5a6b5d", "accent": "#8fd46a", "cyan": "#6cc4b0",
        "amber": "#d9b45a", "red": "#e5686b", "code": "monokai",
    },
}

USER_LABEL = "BOSS"
AGENT_LABEL = "FRIDAY"

# Matches a "🐺 BOSS" style label the model may put at the start of any line
_BOSS_LABEL = re.compile(r"^[ \t]*(?:🐺[ \t]*)?BOSS[ \t]*[:›\-–—>]?[ \t]*", re.MULTILINE)
THINKING_FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
THINKING_PHRASES = ("Thinking", "Reading context", "Working it out", "Putting it together")

FILE_TOOLS = {"edit_file", "write_file"}
FAIL_PREFIXES = ("Tool error:", "Permission denied:", "Blocked by", "User declined")
SIDE_MIN_WIDTH = 110
COMPACT_WIDTH = 110

BUILTIN_COMMANDS: list[tuple[str, str, str]] = [
    ("MODES & CONTEXT", "/model [n|name]", "list installed models or switch"),
    ("MODES & CONTEXT", "/plan", "toggle read-only planning mode"),
    ("MODES & CONTEXT", "/context", "context and token usage"),
    ("MODES & CONTEXT", "/compact", "summarise history to free context"),
    ("MODES & CONTEXT", "/rewind [n]", "undo the last turn(s)"),
    ("WORKFLOWS", "/batch <task>", "split work into isolated worktrees"),
    ("WORKFLOWS", "/code-review [PR]", "review the diff or a GitHub PR"),
    ("WORKFLOWS", "/security-review [PR]", "check for common security risks"),
    ("SESSIONS", "/agents", "list other active sessions"),
    ("SESSIONS", "/msg <id> <text>", "message another session"),
    ("UTILITIES", "/voice", "transcribe a voice prompt and run it"),
    ("UTILITIES", "/insights", "open the local usage report"),
    ("UTILITIES", "/theme <name>", "midnight · ember · forest"),
    ("UTILITIES", "/panel", "toggle the side panel (F2)"),
    ("UTILITIES", "/clear", "clear the transcript (^L)"),
    ("UTILITIES", "/help", "print this guide"),
    ("UTILITIES", "/exit", "close FRIDAY (^Q)"),
]

HISTORY_PATH = Path(os.getenv("FRIDAY_HOME", Path.home() / ".friday")) / "terminal_history"
HISTORY_LIMIT = 500


def _theme(name: str) -> Theme:
    p = THEMES[name]
    return Theme(
        name=f"friday-{name}",
        primary=p["accent"], secondary=p["cyan"], accent=p["accent"],
        warning=p["amber"], error=p["red"], success=p["accent"],
        foreground=p["text"], background=p["bg"], surface=p["surface"], panel=p["panel"],
        dark=True,
        variables={
            "fx-muted": p["muted"], "fx-rule": p["rule"],
            "input-cursor-background": p["accent"], "input-cursor-foreground": p["bg"],
            "input-selection-background": f"{p['accent']} 25%",
            "footer-background": p["surface"], "scrollbar": p["rule"],
            "scrollbar-hover": p["muted"], "scrollbar-active": p["accent"],
            "scrollbar-background": p["bg"], "scrollbar-background-hover": p["bg"],
            "scrollbar-background-active": p["bg"], "scrollbar-corner-color": p["bg"],
            "block-cursor-background": p["accent"], "block-cursor-foreground": p["bg"],
        },
    )


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 60}:{seconds % 60:02d}"


def _short(value: Any, limit: int = 72) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _kfmt(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def _looks_like_diff(text: str) -> bool:
    head = text[:4000]
    return bool(re.search(r"^(@@ |--- |\+\+\+ )", head, re.MULTILINE))


def _tool_target(arguments: dict[str, Any]) -> str:
    for key in ("command", "path", "pattern", "query", "url", "prompt", "target", "name", "message", "title"):
        if arguments.get(key):
            return str(arguments[key])
    if isinstance(arguments.get("tasks"), list):
        return f"{len(arguments['tasks'])} tasks"
    return ""


# --------------------------------------------------------------------------- widgets


class CommandInput(Input):
    """Prompt line with persistent ↑/↓ history."""

    BINDINGS = [
        Binding("up", "history_prev", "History", show=False),
        Binding("down", "history_next", "History", show=False),
    ]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.history: list[str] = self._load_history()
        self._cursor = len(self.history)
        self._draft = ""

    @staticmethod
    def _load_history() -> list[str]:
        try:
            lines = HISTORY_PATH.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [line for line in lines if line.strip()][-HISTORY_LIMIT:]

    def remember(self, line: str) -> None:
        if line and (not self.history or self.history[-1] != line):
            self.history.append(line)
            self.history = self.history[-HISTORY_LIMIT:]
            try:
                HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
                HISTORY_PATH.write_text("\n".join(self.history) + "\n", encoding="utf-8")
            except OSError:
                pass
        self._cursor = len(self.history)
        self._draft = ""

    def action_history_prev(self) -> None:
        if not self.history or self._cursor == 0:
            return
        if self._cursor == len(self.history):
            self._draft = self.value
        self._cursor -= 1
        self.value = self.history[self._cursor]
        self.cursor_position = len(self.value)

    def action_history_next(self) -> None:
        if self._cursor >= len(self.history):
            return
        self._cursor += 1
        self.value = self._draft if self._cursor == len(self.history) else self.history[self._cursor]
        self.cursor_position = len(self.value)


class ApprovalScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("y", "allow", "Allow", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("a", "always", "Always allow", show=False),
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, event: dict[str, Any], palette: dict[str, str]):
        super().__init__()
        self.event = event
        self.palette = palette

    def compose(self) -> ComposeResult:
        p = self.palette
        tool = self.event.get("tool", "unknown")
        reason = self.event.get("reason") or "This action changes files, runs a command or uses the network."
        arguments = json.dumps(self.event.get("arguments", {}), ensure_ascii=False, indent=2)
        preview = str(self.event.get("preview") or "")
        with Vertical(id="approval-dialog"):
            yield Static(Text.assemble(
                ("▲ PERMISSION REQUIRED", f"bold {p['amber']}"), ("   ", ""), (tool, f"bold {p['cyan']}"),
            ), id="approval-title")
            yield Static(Text(_short(reason, 300), style=p["muted"]), id="approval-reason")
            with VerticalScroll(id="approval-body"):
                yield Static(Text("ARGUMENTS", style=f"bold {p['muted']}"), classes="approval-label")
                yield Static(Syntax(arguments[:4000], "json", theme=p["code"], background_color=p["surface"],
                                    word_wrap=True), id="approval-arguments")
                if preview:
                    yield Static(Text("PREVIEW", style=f"bold {p['muted']}"), classes="approval-label")
                    lexer = "diff" if _looks_like_diff(preview) else "markdown"
                    yield Static(Syntax(preview[:8000], lexer, theme=p["code"], background_color=p["surface"],
                                        word_wrap=True), id="approval-preview")
            with Horizontal(id="approval-actions"):
                yield Button(Text("[Y] allow once"), id="allow")
                yield Button(Text("[A] always allow"), id="always")
                yield Button(Text("[N] deny"), id="deny")

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id or "deny")


# --------------------------------------------------------------------------- app


class FridayAgentApp(App[None]):
    TITLE = "FRIDAY"
    SUB_TITLE = "OPERATOR CONSOLE"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt / quit", priority=True),
        Binding("ctrl+q", "quit_session", "Quit", priority=True),
        Binding("ctrl+l", "clear_transcript", "Clear", priority=True),
        Binding("f2", "toggle_panel", "Panel", priority=True),
        Binding("ctrl+p", "toggle_plan", "Plan", priority=True),
        Binding("pageup", "scroll_transcript(-1)", show=False),
        Binding("pagedown", "scroll_transcript(1)", show=False),
    ]
    CSS = """
    Screen { background: $background; color: $foreground; layers: base overlay; }

    #hud { height: 1; padding: 0 1; background: $surface; color: $foreground; }
    #hud-rule { height: 1; padding: 0 1; background: $background; color: $fx-rule; }

    #work-area { height: 1fr; width: 100%; }
    #main { width: 1fr; min-width: 40; height: 1fr; }
    #transcript {
        height: 1fr; margin: 0 0 0 1; padding: 0 1;
        background: $background; color: $foreground;
        border: solid $fx-rule; border-title-color: $fx-muted; border-title-align: left;
        scrollbar-size-vertical: 1;
    }
    #transcript:focus { border: solid $fx-muted; }
    #stream { height: auto; max-height: 14; margin: 0 1 0 2; padding: 0 1; color: $foreground; }
    #thinking { height: 1; margin: 0 2; color: $secondary; }

    #side {
        width: 38; height: 1fr; margin: 0 1 0 1; padding: 0 1;
        background: $surface; border: solid $fx-rule;
        border-title-color: $fx-muted; border-title-align: left;
        scrollbar-size-vertical: 1;
    }
    #side Static { height: auto; }
    .side-block { margin-bottom: 1; }
    #guide-title { color: $fx-muted; text-style: bold; }

    #prompt-row { height: 3; margin: 0 1; border: solid $fx-rule; background: $surface; }
    #prompt-row.-focused { border: solid $primary 60%; }
    #prompt-row.-busy { border: solid $warning 50%; }
    #prompt-glyph { width: 3; height: 1; padding: 0 0 0 1; color: $primary; text-style: bold; }
    #prompt { height: 1; border: none; padding: 0; background: $surface; color: $foreground; }
    #prompt:focus { border: none; }
    #prompt > .input--placeholder { color: $fx-muted; }
    #prompt > .input--suggestion { color: $fx-muted; }

    #status { height: 1; padding: 0 1; background: $surface; color: $fx-muted; }

    ApprovalScreen { align: center middle; background: $background 70%; }
    #approval-dialog {
        width: 96; max-width: 94%; height: auto; max-height: 88%;
        padding: 1 2; background: $surface; border: solid $warning 70%;
    }
    #approval-title { height: 1; }
    #approval-reason { height: auto; margin: 1 0; }
    #approval-body { height: auto; max-height: 26; }
    .approval-label { height: 1; margin-top: 1; }
    #approval-arguments, #approval-preview { height: auto; }
    #approval-actions { height: 3; margin-top: 1; align: right middle; }
    Button {
        margin-left: 1; min-width: 14; height: 3; border: none;
        background: $panel; color: $foreground; text-style: none;
    }
    Button:hover, Button:focus { background: $fx-rule; text-style: bold; }
    Button#allow { color: $primary; }
    Button#always { color: $secondary; }
    Button#deny { color: $error; }
    """

    def __init__(self, api: str | None = None, theme: str | None = None):
        super().__init__()
        self.api = (api or os.getenv("FRIDAY_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        selected = theme or os.getenv("FRIDAY_THEME", "midnight")
        self.theme_name = selected if selected in THEMES else "midnight"
        for name in THEMES:
            self.register_theme(_theme(name))
        self.theme = f"friday-{self.theme_name}"

        self.workspace_dir = Path(os.getenv("FRIDAY_WORKSPACE", os.getcwd()))
        self.client: httpx.AsyncClient | None = None
        self.session_id = ""
        self.model = "—"
        self.workspace = "—"
        self.mode = "default"
        self.network = os.getenv("FRIDAY_NETWORK", "ask")
        self.num_ctx = 0
        self.max_steps = 0
        self.link = "connecting"  # connecting | online | offline | degraded
        self.custom_commands: dict[str, str] = {}

        self.git_branch = ""
        self.git_changes = 0
        self.git_state = "unknown"  # unknown | repo | none

        self.busy = False
        self.awaiting_approval = False
        self.side_visible = True
        self.output = ""
        self._last_stream_render = 0.0

        # per-turn state
        self.current_task = ""
        self.turn_started_at: float | None = None
        self.turn_tools: list[dict[str, Any]] = []
        self.turn_files: list[str] = []
        self.turn_errors = 0
        self.todos: list[dict[str, str]] = []
        self.undo_count = 0
        self.usage = {"in": 0, "out": 0}
        self.context_tokens = 0
        self.context_messages = 0
        self._pending_tools: dict[str, dict[str, Any]] = {}
        self._approval_screen: ApprovalScreen | None = None

        # thinking indicator
        self.thinking_status = "idle"
        self.thinking_started_at: float | None = None
        self.thinking_elapsed = 0.0
        self.thinking_frame = 0
        self.thinking_timer: Any | None = None

    # ------------------------------------------------------------------ layout

    @property
    def palette(self) -> dict[str, str]:
        return THEMES[self.theme_name]

    def compose(self) -> ComposeResult:
        yield Static("", id="hud")
        yield Static("", id="hud-rule")
        with Horizontal(id="work-area"):
            with Vertical(id="main"):
                yield RichLog(id="transcript", wrap=True, markup=False, highlight=False, max_lines=6000)
                yield Static("", id="stream")
                yield Static("", id="thinking")
            with VerticalScroll(id="side"):
                yield Static("", id="side-task", classes="side-block")
                yield Static("", id="side-todos", classes="side-block")
                yield Static("", id="side-tools", classes="side-block")
                yield Static("", id="side-context", classes="side-block")
                yield Static("", id="side-git", classes="side-block")
                yield Static("COMMANDS", id="guide-title")
                yield Static("", id="guide-content")
        with Horizontal(id="prompt-row"):
            yield Static("❯", id="prompt-glyph")
            yield CommandInput(
                placeholder="Ask FRIDAY to inspect, edit, run or test this project · /help",
                id="prompt", max_length=4000,
                suggester=SuggestFromList(self._command_names(), case_sensitive=False),
            )
        yield Static("", id="status")

    async def on_mount(self) -> None:
        self.query_one("#transcript", RichLog).border_title = " transcript "
        self.query_one("#side", VerticalScroll).border_title = " operator "
        self._render_all()
        self._apply_layout(self.size.width)
        self.set_interval(1.0, self._tick)
        self.query_one("#prompt", Input).focus()

        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))
        self.run_worker(self._refresh_git(), group="git", exclusive=True, exit_on_error=False)
        try:
            session = await self._json("POST", "/api/agent/sessions", {})
            self.session_id = session["id"]
            self.mode = session.get("mode", self.mode)
            self.model = session.get("model") or self.model
            self.workspace = session.get("workspace") or self.workspace
            self.num_ctx = int(session.get("num_ctx") or 0)
            self.max_steps = int(session.get("max_steps") or 0)
            self.undo_count = int(session.get("undo_count") or 0)
            usage = session.get("usage")
            if isinstance(usage, dict):
                self.usage = {"in": int(usage.get("in", 0)), "out": int(usage.get("out", 0))}
            self.todos = list(session.get("todos") or [])
            self.link = "online"
            try:
                self.custom_commands = await self._json("GET", "/api/agent/commands") or {}
            except Exception:
                self.custom_commands = {}
            self._refresh_suggester()
            self._write_system(
                f"session {self.session_id[:8]} · model {self.model} · workspace {self.workspace} · mode {self.mode}"
            )
            self.run_worker(self._check_model(), group="model", exit_on_error=False)
            self.run_worker(self._refresh_context(), group="context", exclusive=True, exit_on_error=False)
        except Exception as exc:
            self.link = "offline"
            self._write_error(f"Cannot reach the FRIDAY agent API at {self.api}: {exc}")
            self._write_system("Start FRIDAY (friday start) and relaunch this console.")
        self._render_all()

    async def on_unmount(self) -> None:
        if self.thinking_timer is not None:
            self.thinking_timer.stop()
            self.thinking_timer = None
        if self.client is not None:
            if self.session_id:
                try:
                    await self._json("DELETE", self._session_path)
                except (httpx.HTTPError, RuntimeError):
                    pass
            await self.client.aclose()

    def on_resize(self, event: Resize) -> None:
        self._apply_layout(event.size.width)

    def _apply_layout(self, width: int) -> None:
        self.query_one("#side").display = self.side_visible and width >= SIDE_MIN_WIDTH
        self.query_one("#hud-rule").display = self.size.height >= 20
        self._render_hud()
        self._render_status()

    def on_descendant_focus(self, event: Any) -> None:
        self.query_one("#prompt-row").set_class(self.focused is self.query_one("#prompt"), "-focused")

    def on_descendant_blur(self, event: Any) -> None:
        self.query_one("#prompt-row").set_class(False, "-focused")

    # ------------------------------------------------------------------ api

    @property
    def _session_path(self) -> str:
        return f"/api/agent/sessions/{self.session_id}"

    async def _json(self, method: str, path: str, body: dict | None = None) -> Any:
        if self.client is None:
            raise RuntimeError("The FRIDAY API client has not started.")
        response = await self.client.request(method, self.api + path, json=body)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except (ValueError, json.JSONDecodeError, AttributeError):
                detail = response.text
            raise RuntimeError(f"{response.status_code}: {detail}")
        return response.json()

    async def _check_model(self) -> None:
        try:
            info = await self._json("GET", "/api/agent/models")
        except Exception:
            return
        models = info.get("models") if isinstance(info, dict) else None
        if not models:
            return
        names = {m.get("name") if isinstance(m, dict) else str(m) for m in models}
        if self.model not in names:
            self.link = "degraded"
            self._write_warn(f"Model '{self.model}' is not listed by the local model runner. "
                             f"Available: {', '.join(sorted(n for n in names if n)) or 'none'}")
            self._render_hud()

    async def _refresh_context(self) -> None:
        if not self.session_id:
            return
        try:
            report = await self._json("GET", self._session_path + "/context")
        except Exception:
            return
        if not isinstance(report, dict):
            return
        self.context_tokens = int(report.get("estimated_tokens") or 0)
        self.context_messages = int(report.get("messages") or 0)
        self.usage = {"in": int(report.get("input_tokens") or self.usage["in"]),
                      "out": int(report.get("output_tokens") or self.usage["out"])}
        self.undo_count = int(report.get("undo_count") or self.undo_count)
        self._render_side()

    async def _refresh_git(self) -> None:
        async def git(*args: str) -> str | None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "git", "-C", str(self.workspace_dir), *args,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await asyncio.wait_for(proc.communicate(), timeout=4)
            except (OSError, asyncio.TimeoutError):
                return None
            return out.decode("utf-8", "replace") if proc.returncode == 0 else None

        branch = await git("rev-parse", "--abbrev-ref", "HEAD")
        if branch is None:
            self.git_state, self.git_branch, self.git_changes = "none", "", 0
        else:
            status = await git("status", "--porcelain") or ""
            self.git_state = "repo"
            self.git_branch = branch.strip()
            self.git_changes = len([line for line in status.splitlines() if line.strip()])
        self._render_hud()
        self._render_side()

    # ------------------------------------------------------------------ transcript

    def _log(self) -> RichLog:
        return self.query_one("#transcript", RichLog)

    def _write(self, renderable: Any) -> None:
        self._log().write(renderable)

    def _stamp(self) -> str:
        return time.strftime("%H:%M:%S")

    def _write_system(self, text: str) -> None:
        p = self.palette
        self._write(Text.assemble(("· ", p["muted"]), (text, p["muted"])))

    def _write_warn(self, text: str) -> None:
        p = self.palette
        self._write(Text.assemble(("▲ ", f"bold {p['amber']}"), (text, p["amber"])))

    def _write_error(self, text: str) -> None:
        p = self.palette
        self._write(Text.assemble(("✕ ERROR  ", f"bold {p['red']}"), (text, p["red"])))

    def _write_user(self, prompt: str) -> None:
        p = self.palette
        self._write(Text(""))
        self._write(Text.assemble(
            ("❯ ", f"bold {p['accent']}"), (USER_LABEL, f"bold {p['text']}"), ("  " + self._stamp(), p["muted"]),
        ))
        self._write(Padding(Text(prompt, style=p["text"]), (0, 0, 0, 2)))

    def _reply_renderable(self, text: str) -> Any:
        p = self.palette
        return Markdown(text, code_theme=p["code"], inline_code_theme=p["code"], hyperlinks=False)

    def _write_agent(self, text: str) -> None:
        p = self.palette
        elapsed = time.perf_counter() - self.turn_started_at if self.turn_started_at else 0.0
        meta = f"  {self._stamp()} · {elapsed:.1f}s"
        if self.turn_tools:
            meta += f" · {len(self.turn_tools)} tool{'s' if len(self.turn_tools) != 1 else ''}"
        self._write(Text.assemble(("◆ ", f"bold {p['accent']}"), (AGENT_LABEL, f"bold {p['accent']}"), (meta, p["muted"])))
        self._write(Padding(self._reply_renderable(text), (0, 0, 0, 2)))

    def _write_tool_start(self, event: dict[str, Any]) -> None:
        p = self.palette
        args = event.get("arguments") or {}
        target = _tool_target(args)
        line = Text.assemble(
            ("├─ ", p["rule"]), ("▸ ", p["cyan"]), (str(event.get("tool", "?")), f"bold {p['cyan']}"),
            ("  " + _short(target, 90) if target else "", p["text"]),
        )
        line.no_wrap, line.overflow = True, "ellipsis"
        self._write(line)

    def _write_tool_result(self, event: dict[str, Any], elapsed: float | None) -> None:
        p = self.palette
        result = str(event.get("result") or "")
        failed = result.startswith(FAIL_PREFIXES)
        lines = result.count("\n") + 1 if result else 0
        timing = f"{elapsed:.1f}s  " if elapsed is not None else ""
        if failed:
            line = Text.assemble(("│   ", p["rule"]), ("✕ ", f"bold {p['red']}"), (timing, p["muted"]),
                                 (_short(result, 140), p["red"]))
            line.no_wrap, line.overflow = True, "ellipsis"
        else:
            first = next((ln for ln in result.splitlines() if ln.strip()), "")
            summary = f"{lines} line{'s' if lines != 1 else ''}" + (f" · {_short(first, 80)}" if first else "")
            line = Text.assemble(("│   ", p["rule"]), ("✓ ", f"bold {p['accent']}"), (timing, p["muted"]),
                                 (summary, p["muted"]))
            line.no_wrap, line.overflow = True, "ellipsis"
        self._write(line)
        if not failed and _looks_like_diff(result):
            diff = "\n".join(result.splitlines()[:60])
            self._write(Padding(Syntax(diff, "diff", theme=p["code"], background_color=p["bg"], word_wrap=True),
                                (0, 0, 0, 4)))

    def _write_turn_summary(self, interrupted: bool = False) -> None:
        p = self.palette
        parts: list[tuple[str, str]] = [("└─ ", p["rule"])]
        if interrupted:
            parts.append(("⏹ interrupted", f"bold {p['amber']}"))
        elif self.turn_errors:
            parts.append((f"done with {self.turn_errors} failed step{'s' if self.turn_errors != 1 else ''}", p["amber"]))
        else:
            parts.append(("done", p["accent"]))
        if self.turn_tools:
            parts.append((f" · {len(self.turn_tools)} tool calls", p["muted"]))
        if self.turn_files:
            files = ", ".join(dict.fromkeys(self.turn_files))
            parts.append((f" · modified {_short(files, 90)}", p["amber"]))
        if self.undo_count:
            parts.append((f" · /rewind available ({self.undo_count})", p["muted"]))
        self._write(Text.assemble(*parts))

    def _clean_reply(self, text: str) -> str:
        """Remove '🐺 BOSS' labels the model adds at the start of any line."""
        return _BOSS_LABEL.sub("", text).lstrip()

    # ------------------------------------------------------------------ chrome rendering

    def _render_all(self) -> None:
        self._render_hud()
        self._render_side()
        self._update_guide()
        self._render_status()

    def _tick(self) -> None:
        self._render_hud()
        if self.busy:
            self._render_status()
            self._render_side_task()

    def _render_hud(self) -> None:
        p = self.palette
        width = self.size.width or 120
        link_style = {"online": p["accent"], "connecting": p["amber"], "degraded": p["amber"]}.get(self.link, p["red"])
        link_label = {"online": "LINK", "connecting": "CONNECTING", "degraded": "MODEL?"}.get(self.link, "OFFLINE")
        left = Text.assemble(
            ("▌", p["accent"]), ("FRIDAY", f"bold {p['accent']}"), ("  ", ""),
            ("● ", link_style), (link_label, f"bold {link_style}"),
        )
        sep = ("  │  ", p["muted"])
        left.append_text(Text.assemble(sep, ("model ", p["muted"]), (self.model, p["text"])))
        if width >= COMPACT_WIDTH:
            left.append_text(Text.assemble(sep, ("ws ", p["muted"]), (self.workspace, p["text"])))
            if self.git_state == "repo":
                dirty = f" ±{self.git_changes}" if self.git_changes else " ✓"
                left.append_text(Text.assemble(sep, ("⎇ ", p["cyan"]), (self.git_branch, p["cyan"]),
                                               (dirty, p["amber"] if self.git_changes else p["muted"])))
        mode_style = f"bold {p['amber']}" if self.mode == "plan" else p["text"]
        left.append_text(Text.assemble(sep, ("mode ", p["muted"]), (self.mode.upper(), mode_style)))
        if width >= COMPACT_WIDTH:
            left.append_text(Text.assemble(sep, ("net ", p["muted"]), (self.network, p["text"])))
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(left, Text(time.strftime("%H:%M:%S"), style=p["muted"]))
        self.query_one("#hud", Static).update(grid)
        self.query_one("#hud-rule", Static).update(Text("─" * max(width - 2, 1), style=p["rule"]))

    def _render_status(self) -> None:
        p = self.palette
        if self.awaiting_approval:
            state = Text.assemble(("▲ AWAITING APPROVAL", f"bold {p['amber']}"))
        elif self.busy:
            elapsed = time.perf_counter() - self.turn_started_at if self.turn_started_at else 0.0
            frame = THINKING_FRAMES[int(time.perf_counter() * 8) % len(THINKING_FRAMES)]
            state = Text.assemble((f"{frame} RUNNING ", f"bold {p['cyan']}"), (_clock(elapsed), p["muted"]))
        elif self.link == "offline":
            state = Text.assemble(("✕ OFFLINE", f"bold {p['red']}"))
        else:
            state = Text.assemble(("◆ READY", f"bold {p['accent']}"))
        state.append_text(Text.assemble(
            ("   mode ", p["muted"]), (self.mode, p["text"]),
            ("   perm ", p["muted"]), ("plan:read-only" if self.mode == "plan" else f"net:{self.network}", p["text"]),
            ("   theme ", p["muted"]), (self.theme_name, p["text"]),
        ))
        keys = [("⏎", "send"), ("^C", "interrupt" if self.busy else "quit"), ("↑↓", "history"),
                ("→", "complete"), ("^P", "plan"), ("F2", "panel"), ("^L", "clear")]
        if (self.size.width or 120) < COMPACT_WIDTH:
            keys = keys[:3]
        hint = Text()
        for key, label in keys:
            hint.append(f" {key}", style=f"bold {p['text']}")
            hint.append(f" {label} ", style=p["muted"])
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(state, hint)
        self.query_one("#status", Static).update(grid)
        row = self.query_one("#prompt-row")
        row.set_class(self.busy, "-busy")

    def _section(self, title: str, body: Text | Group | str) -> Group:
        p = self.palette
        header = Text.assemble(("── ", p["rule"]), (title, f"bold {p['muted']}"), (" " + "─" * 30, p["rule"]))
        header.truncate(34)
        return Group(header, body if not isinstance(body, str) else Text(body, style=p["text"]))

    def _render_side(self) -> None:
        self._render_side_task()
        p = self.palette

        if self.todos:
            todo = Text()
            done = sum(1 for t in self.todos if t.get("status") == "completed")
            todo.append(f"{done}/{len(self.todos)} complete\n", style=p["muted"])
            for item in self.todos[:14]:
                status = item.get("status", "pending")
                glyph, style = {"completed": ("■", p["accent"]), "in_progress": ("◧", p["amber"])}.get(
                    status, ("□", p["muted"]))
                todo.append(f"{glyph} ", style=style)
                todo.append(_short(item.get("content", ""), 30) + "\n",
                            style=p["muted"] if status == "completed" else p["text"])
            todo.rstrip()
        else:
            todo = Text("no task list", style=p["muted"])
        self.query_one("#side-todos", Static).update(self._section("PROGRESS", todo))

        tools = Text()
        if self._pending_tools:
            for info in self._pending_tools.values():
                tools.append("▸ ", style=p["cyan"])
                tools.append(info["tool"] + "\n", style=f"bold {p['cyan']}")
        recent = self.turn_tools[-6:]
        for info in recent:
            ok = info.get("ok")
            tools.append("✓ " if ok else "✕ " if ok is False else "· ", style=p["accent"] if ok else p["red"] if ok is False else p["muted"])
            tools.append(_short(info["tool"], 16), style=p["text"])
            if info.get("elapsed") is not None:
                tools.append(f"  {info['elapsed']:.1f}s", style=p["muted"])
            tools.append("\n")
        tools.rstrip()
        if not tools.plain:
            tools = Text("idle", style=p["muted"])
        self.query_one("#side-tools", Static).update(self._section("TOOLS", tools))

        ctx = Text()
        if self.num_ctx:
            ratio = min(self.context_tokens / self.num_ctx, 1.0)
            cells = 20
            filled = int(round(ratio * cells))
            bar_style = p["red"] if ratio > 0.85 else p["amber"] if ratio > 0.6 else p["accent"]
            ctx.append("▰" * filled, style=bar_style)
            ctx.append("▱" * (cells - filled), style=p["rule"])
            ctx.append(f" {ratio * 100:.0f}%\n", style=p["text"])
            ctx.append(f"~{_kfmt(self.context_tokens)} / {_kfmt(self.num_ctx)} tokens\n", style=p["muted"])
        ctx.append(f"usage in {_kfmt(self.usage['in'])} · out {_kfmt(self.usage['out'])}\n", style=p["muted"])
        ctx.append(f"{self.context_messages} messages · undo {self.undo_count}", style=p["muted"])
        model = Text.assemble(("\n", ""), (self.model, f"bold {p['text']}"))
        if self.max_steps:
            model.append(f"\nmax steps {self.max_steps}", style=p["muted"])
        self.query_one("#side-context", Static).update(self._section("CONTEXT · MODEL", Group(ctx, model)))

        if self.git_state == "repo":
            git = Text.assemble(("⎇ ", p["cyan"]), (self.git_branch, f"bold {p['cyan']}"), ("\n", ""))
            git.append(f"{self.git_changes} changed file{'s' if self.git_changes != 1 else ''}" if self.git_changes
                       else "working tree clean", style=p["amber"] if self.git_changes else p["muted"])
        elif self.git_state == "none":
            git = Text("not a git repository", style=p["muted"])
        else:
            git = Text("checking…", style=p["muted"])
        self.query_one("#side-git", Static).update(self._section("GIT", git))

    def _render_side_task(self) -> None:
        p = self.palette
        if self.current_task:
            body = Text(_short(self.current_task, 110), style=p["text"])
            if self.busy and self.turn_started_at:
                body.append(f"\nrunning {_clock(time.perf_counter() - self.turn_started_at)}", style=p["cyan"])
            elif self.turn_started_at is None:
                body.append("\nlast task", style=p["muted"])
        else:
            body = Text("waiting for instructions", style=p["muted"])
        self.query_one("#side-task", Static).update(self._section("TASK", body))

    def _command_names(self) -> list[str]:
        names = [cmd.split()[0] for _, cmd, _ in BUILTIN_COMMANDS]
        names += ["/quit", "/theme midnight", "/theme ember", "/theme forest"]
        names += [f"/{name}" for name in sorted(self.custom_commands)]
        return names

    def _refresh_suggester(self) -> None:
        self.query_one("#prompt", Input).suggester = SuggestFromList(self._command_names(), case_sensitive=False)

    def _guide_text(self) -> Text:
        p = self.palette
        text = Text()
        last_group = ""
        for group, cmd, desc in BUILTIN_COMMANDS:
            if group != last_group:
                if last_group:
                    text.append("\n")
                text.append(group + "\n", style=f"bold {p['muted']}")
                last_group = group
            text.append(cmd + "\n", style=p["accent"])
            text.append(f"  {desc}\n", style=p["muted"])
        if self.custom_commands:
            text.append("\nSHORTCUTS\n", style=f"bold {p['muted']}")
            for name, prompt in sorted(self.custom_commands.items()):
                summary = prompt.strip().splitlines()[0] if prompt.strip() else "custom prompt"
                text.append(f"/{name}\n", style=p["cyan"])
                text.append(f"  {_short(summary, 48)}\n", style=p["muted"])
        text.append("\nKEYS\n", style=f"bold {p['muted']}")
        for key, desc in [("Enter", "send"), ("Ctrl+C", "interrupt turn / quit"), ("Ctrl+Q", "quit"),
                          ("↑ / ↓", "prompt history"), ("→", "accept completion"), ("Ctrl+P", "plan mode"),
                          ("F2", "side panel"), ("PgUp/PgDn", "scroll transcript"),
                          ("Approval", "Y once · A always · N/Esc deny")]:
            text.append(f"{key:<11}", style=p["text"])
            text.append(desc + "\n", style=p["muted"])
        text.rstrip()
        return text

    def _update_guide(self) -> None:
        self.query_one("#guide-content", Static).update(self._guide_text())

    def _update_status(self) -> None:
        """Kept for compatibility: re-render all chrome after a mode/theme change."""
        self._render_all()

    # ------------------------------------------------------------------ thinking indicator

    def _start_thinking(self) -> None:
        if self.thinking_timer is not None:
            self.thinking_timer.stop()
        self.thinking_status = "thinking"
        self.thinking_started_at = time.perf_counter()
        self.thinking_elapsed = 0.0
        self.thinking_frame = 0
        self.query_one("#stream", Static).update("")
        self._render_thinking()
        self.thinking_timer = self.set_interval(0.1, self._tick_thinking)

    def _tick_thinking(self) -> None:
        if self.thinking_started_at is None:
            return
        self.thinking_elapsed = time.perf_counter() - self.thinking_started_at
        self.thinking_frame = (self.thinking_frame + 1) % len(THINKING_FRAMES)
        self._render_thinking()

    def _set_streaming(self) -> None:
        if self.thinking_status != "thinking":
            return
        self.thinking_status = "streaming"
        self._render_thinking()

    def _finish_thinking(self) -> None:
        if self.thinking_status not in {"thinking", "streaming"}:
            return
        if self.thinking_started_at is not None:
            self.thinking_elapsed = time.perf_counter() - self.thinking_started_at
        self.thinking_started_at = None
        self.thinking_status = "done"
        if self.thinking_timer is not None:
            self.thinking_timer.stop()
            self.thinking_timer = None
        self._render_thinking()

    def _reset_thinking(self) -> None:
        if self.thinking_timer is not None:
            self.thinking_timer.stop()
            self.thinking_timer = None
        self.thinking_started_at = None
        self.thinking_elapsed = 0.0
        self.thinking_status = "idle"
        self.query_one("#thinking", Static).update("")

    def _render_thinking(self) -> None:
        p = self.palette
        clock = _clock(self.thinking_elapsed)
        if self.thinking_status == "thinking":
            phrase = THINKING_PHRASES[int(self.thinking_elapsed // 2.5) % len(THINKING_PHRASES)]
            text = Text.assemble((THINKING_FRAMES[self.thinking_frame] + "  ", f"bold {p['cyan']}"),
                                 (f"{phrase}...", p["cyan"]), (f"  {clock}", p["muted"]))
        elif self.thinking_status == "streaming":
            text = Text.assemble(("▍ ", f"bold {p['accent']}"), ("Streaming response", p["accent"]),
                                 (f"  {clock}", p["muted"]))
        elif self.thinking_status == "done":
            text = Text(f"Thought for {int(self.thinking_elapsed)}s", style=p["muted"])
        else:
            text = Text("")
        self.query_one("#thinking", Static).update(text)

    # ------------------------------------------------------------------ input & commands

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if not prompt or self.busy:
            return
        event.input.value = ""
        if isinstance(event.input, CommandInput):
            event.input.remember(prompt)
        self.run_worker(self._process(prompt), name="friday-agent-turn", group="agent",
                        exclusive=True, exit_on_error=False)

    async def _process(self, prompt: str) -> None:
        self.busy = True
        self._render_status()
        try:
            if prompt.startswith("/"):
                await self._command(prompt)
            else:
                await self._run_turn(prompt)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._write_error(str(exc))
        finally:
            self.busy = False
            self.awaiting_approval = False
            self._render_status()
            self._render_side()
            self.query_one("#prompt", Input).focus()

    async def _command(self, line: str) -> None:
        name, _, arg = line[1:].partition(" ")
        arg = arg.strip()
        if name in {"exit", "quit"}:
            self.exit()
        elif name == "help":
            self._write(Padding(self._guide_text(), (0, 0, 0, 2)))
        elif name == "clear":
            self.action_clear_transcript()
        elif name == "panel":
            self.action_toggle_panel()
        elif name == "plan":
            await self._toggle_plan()
        elif name == "model":
            await self._switch_model(arg)
        elif name == "rewind":
            count = int(arg or "1")
            result = await self._json("POST", self._session_path + "/rewind", {"count": count})
            self.undo_count = int(result.get("undo_count", self.undo_count))
            self._write_system("rewound: " + (", ".join(result.get("rewound", [])) or "nothing"))
            self.run_worker(self._refresh_git(), group="git", exclusive=True, exit_on_error=False)
            await self._refresh_context()
        elif name == "context":
            await self._refresh_context()
            pct = f" ({self.context_tokens / self.num_ctx * 100:.0f}% of {_kfmt(self.num_ctx)})" if self.num_ctx else ""
            self._write_system(f"messages {self.context_messages} · context ~{_kfmt(self.context_tokens)} tokens{pct} · "
                               f"usage in {_kfmt(self.usage['in'])} out {_kfmt(self.usage['out'])} · undo {self.undo_count}")
        elif name == "compact":
            result = await self._json("POST", self._session_path + "/compact", {})
            self._write_system(f"history compacted · {result.get('messages', '?')} messages remain")
            await self._refresh_context()
        elif name == "theme":
            if arg not in THEMES:
                self._write_system("themes: " + ", ".join(THEMES))
            else:
                self.theme_name = arg
                self.theme = f"friday-{arg}"
                self._render_all()
                self._write_system(f"theme {arg}")
        elif name in {"batch", "code-review", "security-review", "msg"}:
            if name == "batch":
                prompt = ("Split this into independent subtasks touching separate files. Use batch_worktrees "
                          f"to create an isolated branch and worktree for each. Task: {arg}")
            elif name == "msg":
                target, _, text = arg.partition(" ")
                if not target or not text:
                    raise ValueError("Usage: /msg <session-id> <message>")
                prompt = f"Use SendMessage to send this exact message to Friday session {target}: {json.dumps(text)}"
            else:
                review = ("Security review for injection, authorization, secrets, path traversal, and unsafe "
                          "subprocess use." if name == "security-review"
                          else "Code review for correctness, edge cases, missing tests, and regressions.")
                prompt = (f"{review} Use git_review_diff{' with PR number ' + arg if arg else ' for the working diff'}. "
                          "Cite file and line references.")
            await self._run_turn(prompt, label=line)
        elif name == "agents":
            sessions = await self._json("GET", "/api/agent/sessions")
            others = [item for item in sessions if item["id"] != self.session_id[:8]]
            if not others:
                self._write_system("no other agent sessions")
            for item in others:
                self._write_system(f"{item['id']}  {item['workspace']}  {item['mode']}{'  busy' if item.get('busy') else ''}")
        elif name == "voice":
            self._write_system("listening…")
            result = await self._json("POST", "/api/agent/voice", {})
            self._write_system("heard: " + (result.get("text") or "nothing"))
            if result.get("text"):
                await self._run_turn(result["text"])
        elif name == "insights":
            result = await self._json("GET", "/api/agent/insights")
            self._write_system("insights report opened: " + result.get("path", ""))
        elif name in self.custom_commands:
            await self._run_turn(self.custom_commands[name].replace("$ARGUMENTS", arg), label=line)
        else:
            raise ValueError(f"Unknown command /{name}. Try /help.")

    async def _switch_model(self, arg: str) -> None:
        info = await self._json("GET", "/api/agent/models")
        names = sorted({m.get("name") if isinstance(m, dict) else str(m) for m in info.get("models") or []} - {None, ""})
        if not arg:
            if not names:
                self._write_warn("no installed models found · is Ollama running? (ollama pull llama3.2)")
                return
            for index, model in enumerate(names, 1):
                marker = "●" if model == self.model else " "
                self._write_system(f"{marker} {index:>2}  {model}")
            self._write_system("switch with /model <number> or /model <name>")
            return
        target = names[int(arg) - 1] if arg.isdigit() and 1 <= int(arg) <= len(names) else arg
        result = await self._json("PATCH", self._session_path, {"model": target})
        self.model = result.get("model", target)
        self._render_hud()
        self._render_side()
        self._write_system(f"model {self.model}")

    async def _toggle_plan(self) -> None:
        target = "default" if self.mode == "plan" else "plan"
        result = await self._json("PATCH", self._session_path, {"mode": target})
        self.mode = result.get("mode", target)
        if self.mode == "plan":
            self._write_warn("plan mode ON · read-only: FRIDAY will inspect and propose, not change files")
        else:
            self._write_system("plan mode OFF")
        self._render_all()

    # ------------------------------------------------------------------ agent turn

    async def _run_turn(self, prompt: str, label: str | None = None) -> None:
        if self.client is None:
            raise RuntimeError("The FRIDAY API client has not started.")
        if not self.session_id:
            raise RuntimeError("No agent session. Start the FRIDAY backend and relaunch the console.")
        self._write_user(label or prompt)
        self.output = ""
        self.current_task = label or prompt
        self.turn_started_at = time.perf_counter()
        self.turn_tools, self.turn_files, self.turn_errors = [], [], 0
        self._pending_tools.clear()
        self._render_side()
        self._start_thinking()
        interrupted = False
        try:
            async with self.client.stream("POST", self.api + self._session_path + "/turns",
                                          json={"prompt": prompt}) as response:
                if response.is_error:
                    body = (await response.aread()).decode("utf-8", "replace")
                    try:
                        body = json.loads(body).get("detail", body)
                    except (ValueError, AttributeError):
                        pass
                    raise RuntimeError(f"{response.status_code}: {body}")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        await self._event(json.loads(line[6:]))
        except asyncio.CancelledError:
            interrupted = True
            raise
        except httpx.HTTPError as exc:
            self.link = "offline"
            raise RuntimeError(f"Lost connection to the agent API: {exc}") from exc
        finally:
            self._finish_thinking()
            self._flush_reply()
            self._write_turn_summary(interrupted=interrupted)
            self.turn_started_at = None
            self._pending_tools.clear()
            self._render_side()
            self.run_worker(self._refresh_git(), group="git", exclusive=True, exit_on_error=False)
            self.run_worker(self._refresh_context(), group="context", exclusive=True, exit_on_error=False)

    def _flush_reply(self) -> None:
        if self.output.strip():
            self._write_agent(self._clean_reply(self.output))
        self.output = ""
        self.query_one("#stream", Static).update("")

    def _render_stream(self, force: bool = False) -> None:
        now = time.perf_counter()
        if not force and now - self._last_stream_render < 0.06:
            return
        self._last_stream_render = now
        text = self._clean_reply(self.output)
        if len(text) > 2400:
            text = "…" + text[-2400:]
        self.query_one("#stream", Static).update(self._reply_renderable(text))

    async def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "turn_start":
            self.mode = event.get("mode", self.mode)
            self.model = event.get("model", self.model)
            self._render_hud()
        elif kind == "token":
            self._set_streaming()
            self.output += event.get("content", "")
            self._render_stream()
        elif kind == "tool_start":
            # text streamed before a tool call is a narration step: commit it to the transcript
            self._flush_reply()
            self._write_tool_start(event)
            self._pending_tools[str(event.get("id"))] = {"tool": event.get("tool", "?"),
                                                        "started": time.perf_counter(),
                                                        "arguments": event.get("arguments") or {}}
            self._render_side()
        elif kind == "approval_required":
            await self._ask_approval(event)
        elif kind == "tool_result":
            self.mode = event.get("mode", self.mode)
            if "undo_count" in event:
                self.undo_count = int(event["undo_count"])
            pending = self._pending_tools.pop(str(event.get("id")), None)
            elapsed = time.perf_counter() - pending["started"] if pending else None
            result = str(event.get("result") or "")
            ok = not result.startswith(FAIL_PREFIXES)
            if not ok:
                self.turn_errors += 1
            tool = event.get("tool", "?")
            self.turn_tools.append({"tool": tool, "ok": ok, "elapsed": elapsed})
            if ok and tool in FILE_TOOLS and pending and pending["arguments"].get("path"):
                self.turn_files.append(str(pending["arguments"]["path"]))
            if pending is None:
                self._write_tool_start(event)
            self._write_tool_result(event, elapsed)
            self._render_side()
            self._render_hud()
        elif kind == "todos":
            self.todos = list(event.get("items") or event.get("todos") or [])
            self._render_side()
        elif kind == "error":
            message = event.get("message", "Agent turn failed.")
            if "model" in message.lower() or "ollama" in message.lower() or "connect" in message.lower():
                self.link = "degraded"
            self._flush_reply()
            self._write_error(message)
        elif kind == "done":
            self.mode = event.get("mode", self.mode)
            if "todos" in event:
                self.todos = list(event.get("todos") or [])
            if "undo_count" in event:
                self.undo_count = int(event["undo_count"])
            if self.link == "degraded" and not self.turn_errors:
                self.link = "online"
            self._finish_thinking()
            self._render_all()

    async def _ask_approval(self, event: dict[str, Any]) -> None:
        self._flush_reply()
        self.awaiting_approval = True
        self._render_status()
        p = self.palette
        self._write(Text.assemble(("│   ", p["rule"]), ("▲ approval requested", f"bold {p['amber']}"),
                                  (f"  {event.get('tool', '')}", p["muted"])))
        screen = ApprovalScreen(event, self.palette)
        self._approval_screen = screen
        try:
            decision = await self.push_screen_wait(screen) or "deny"
        finally:
            self._approval_screen = None
            self.awaiting_approval = False
            self._render_status()
        await self._json("POST", f"{self._session_path}/approvals/{event['id']}", {"decision": decision})
        label = {"allow": ("allowed once", p["accent"]), "always": ("always allowed", p["cyan"])}.get(
            decision, ("denied", p["red"]))
        self._write(Text.assemble(("│   ", p["rule"]), ("▲ ", p["amber"]), (label[0], f"bold {label[1]}")))

    # ------------------------------------------------------------------ actions

    def action_interrupt(self) -> None:
        if not self.busy:
            self.exit()
            return
        if self._approval_screen is not None:
            self._approval_screen.dismiss("deny")
        self.workers.cancel_group(self, "agent")
        self._write_warn("interrupt sent · stopping the current turn")

    def action_quit_session(self) -> None:
        self.exit()

    def action_clear_transcript(self) -> None:
        self._log().clear()
        self._reset_thinking()

    def action_toggle_panel(self) -> None:
        self.side_visible = not self.side_visible
        self._apply_layout(self.size.width)
        if self.side_visible and self.size.width < SIDE_MIN_WIDTH:
            self._write_system(f"side panel needs at least {SIDE_MIN_WIDTH} columns")

    def action_toggle_plan(self) -> None:
        if self.busy or not self.session_id:
            return
        self.run_worker(self._process("/plan"), group="agent", exclusive=True, exit_on_error=False)

    def action_scroll_transcript(self, direction: int) -> None:
        log = self._log()
        log.scroll_page_down() if direction > 0 else log.scroll_page_up()


def main() -> None:
    FridayAgentApp().run()


if __name__ == "__main__":
    main()

