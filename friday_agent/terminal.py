"""Friday Code: full-screen terminal workspace for the local FRIDAY coding agent.

Layout (wide terminals):

    ✻ Friday Code v4.00                     ● llama3.2 · FRIDAY · ⎇ main ±2 · 16:20
    ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈
       ☼   ▂▃▂          seasonal scene with Patch, the Friday Code mascot     🌳
    ┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈┈ autumn · windy · day ┈┈
     ❯ prompt                                                   ┃ side panel (F2)
     ● Read(src/app.py)                                         ┃ task, todos,
       ⎿  120 lines                                             ┃ tools, context,
     ● markdown reply with code blocks                          ┃ git, commands
    ✻ Thinking… (4s · esc to interrupt)
    ╭──────────────────────────────────────────────────────────────────────────────╮
    │ ❯ input                                                                      │
    ╰──────────────────────────────────────────────────────────────────────────────╯
      ◆ ready · default · net ask                  / commands · ^R retry · ^Y copy

Every request moves through submitted → processing → generating → completed, failed or
cancelled, and is never left loading. All actions go to the real FRIDAY agent API.
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
from rich.theme import Theme as RichTheme
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.suggester import SuggestFromList
from textual.theme import Theme
from textual.widgets import Button, Input, OptionList, RichLog, Static
from textual.widgets.option_list import Option

from .routing import split_trailing_call
from .scene import SEASONS, WEATHERS, Scene, logo

FRIDAY_VERSION = os.getenv("FRIDAY_VERSION", "4.00")
PRODUCT = "Friday Code"

# --------------------------------------------------------------------------- palette
# "friday" is sampled from the Claude Code reference screenshots: background #23212c,
# logo terracotta #d87756, title/mascot rose #d78788, selection blue #b4c6de, text #f0f0f6,
# muted #84848a, dotted rules #66666c. The old bright orange ("ember") is retuned to the
# same warm family so no theme uses it any more.

THEMES: dict[str, dict[str, str]] = {
    "friday": {
        "bg": "#23212c", "surface": "#2b2935", "panel": "#27252f", "border": "#3e3c49", "rule": "#66666c",
        "text": "#f0f0f6", "bright": "#ffffff", "muted": "#84848a", "dim": "#5f5e69",
        "accent": "#d87756", "rose": "#d78788", "info": "#b4c6de", "green": "#8fbf86",
        "amber": "#e0b05a", "red": "#e5686b", "user_bg": "#2d2b38", "code": "dracula",
    },
    "ember": {
        "bg": "#262624", "surface": "#30302e", "panel": "#2b2b29", "border": "#45443f", "rule": "#6b6a64",
        "text": "#faf9f5", "bright": "#ffffff", "muted": "#9c9a92", "dim": "#64635d",
        "accent": "#d97757", "rose": "#e08a6d", "info": "#a9bfd8", "green": "#8fa672",
        "amber": "#d4b07a", "red": "#e5686b", "user_bg": "#32312e", "code": "dracula",
    },
    "midnight": {
        "bg": "#0a0c0d", "surface": "#0e1112", "panel": "#12171a", "border": "#26313a", "rule": "#1e262a",
        "text": "#c8d0cc", "bright": "#eef4f0", "muted": "#5d6a70", "dim": "#3a454a",
        "accent": "#5fd49a", "rose": "#5ec2d6", "info": "#5ec2d6", "green": "#5fd49a",
        "amber": "#e0b05a", "red": "#e5686b", "user_bg": "#11161a", "code": "monokai",
    },
    "forest": {
        "bg": "#080c09", "surface": "#0c110d", "panel": "#111812", "border": "#24332a", "rule": "#1c271e",
        "text": "#c5d0c6", "bright": "#eef4ee", "muted": "#5a6b5d", "dim": "#38453a",
        "accent": "#8fd46a", "rose": "#6cc4b0", "info": "#6cc4b0", "green": "#8fd46a",
        "amber": "#d9b45a", "red": "#e5686b", "user_bg": "#0f1611", "code": "monokai",
    },
}
DEFAULT_THEME = "friday"

USER_LABEL = "BOSS"
AGENT_LABEL = "Friday"

# Matches a "🐺 BOSS" style label the model may put at the start of any line
_BOSS_LABEL = re.compile(r"^[ \t]*(?:🐺[ \t]*)?BOSS[ \t]*[:›\-–—>]?[ \t]*", re.MULTILINE)
SPINNER = ("·", "✢", "✳", "✶", "✻", "✽", "✻", "✶", "✳", "✢")
THINKING_PHRASES = ("Thinking", "Pondering", "Working it out", "Putting it together")
READING_PHRASES = ("Reading the workspace", "Looking around", "Gathering context")
_CODE_BLOCK = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

FILE_TOOLS = {"edit_file", "write_file"}
FAIL_PREFIXES = ("Tool error:", "Permission denied:", "Blocked by", "User declined")
SIDE_MIN_WIDTH = 150
COMPACT_WIDTH = 110
SCENE_MIN_HEIGHT = 32
STALL_SECONDS = float(os.getenv("FRIDAY_STALL_TIMEOUT", "360"))  # > backend's 300 s Ollama timeout
SCENE_FPS = 6

TOOL_TITLES = {
    "read_file": "Read", "edit_file": "Update", "write_file": "Write", "bash": "Bash", "grep": "Search",
    "glob": "Glob", "list_dir": "List", "web_fetch": "Fetch", "todo_write": "Todos", "task": "Task",
    "spawn_subagent": "Agent", "search_knowledge": "Knowledge", "exit_plan_mode": "Plan",
    "git_status": "Git status", "git_diff": "Git diff", "git_log": "Git log", "git_show": "Git show",
    "git_add": "Git add", "git_commit": "Git commit", "git_branch": "Git branch",
    "git_review_diff": "Review diff", "create_pr": "Pull request", "batch_worktrees": "Worktrees",
}

BUILTIN_COMMANDS: list[tuple[str, str, str]] = [
    ("MODES & CONTEXT", "/model [n|name]", "list installed models or switch"),
    ("MODES & CONTEXT", "/plan", "toggle read-only planning mode"),
    ("MODES & CONTEXT", "/tools [auto|on|off]", "when to send workspace tools to the model"),
    ("MODES & CONTEXT", "/context", "context and token usage"),
    ("MODES & CONTEXT", "/compact", "summarise history to free context"),
    ("MODES & CONTEXT", "/rewind [n]", "undo the last turn(s)"),
    ("WORKFLOWS", "/batch <task>", "split work into isolated worktrees"),
    ("WORKFLOWS", "/code-review [PR]", "review the diff or a GitHub PR"),
    ("WORKFLOWS", "/security-review [PR]", "check for common security risks"),
    ("SESSIONS", "/agents", "list other active sessions"),
    ("SESSIONS", "/msg <id> <text>", "message another session"),
    ("UTILITIES", "/retry", "ask the last failed question again (^R)"),
    ("UTILITIES", "/copy [all]", "copy the last code block or reply (^Y)"),
    ("UTILITIES", "/stats", "speed of the last answer; toggle detailed timings"),
    ("UTILITIES", "/scene [option]", "on · off · next · cycle · calendar · season · weather · day · night"),
    ("UTILITIES", "/voice", "transcribe a voice prompt and run it"),
    ("UTILITIES", "/insights", "open the local usage report"),
    ("UTILITIES", "/theme <name>", "friday · ember · midnight · forest"),
    ("UTILITIES", "/panel", "toggle the side panel (F2)"),
    ("UTILITIES", "/clear", "clear the transcript (^L)"),
    ("UTILITIES", "/help", "print this guide"),
    ("UTILITIES", "/exit", "close Friday Code (^Q)"),
]

HISTORY_PATH = Path(os.getenv("FRIDAY_HOME", Path.home() / ".friday")) / "terminal_history"
HISTORY_LIMIT = 500


def _theme(name: str) -> Theme:
    p = THEMES[name]
    return Theme(
        name=f"friday-{name}",
        primary=p["accent"], secondary=p["info"], accent=p["accent"],
        warning=p["amber"], error=p["red"], success=p["green"],
        foreground=p["text"], background=p["bg"], surface=p["surface"], panel=p["panel"],
        dark=True,
        variables={
            "fx-muted": p["muted"], "fx-rule": p["rule"], "fx-border": p["border"], "fx-dim": p["dim"],
            "fx-rose": p["rose"],
            "input-cursor-background": p["accent"], "input-cursor-foreground": p["bg"],
            "input-selection-background": f"{p['accent']} 25%",
            "footer-background": p["bg"], "scrollbar": p["border"],
            "scrollbar-hover": p["muted"], "scrollbar-active": p["accent"],
            "scrollbar-background": p["bg"], "scrollbar-background-hover": p["bg"],
            "scrollbar-background-active": p["bg"], "scrollbar-corner-color": p["bg"],
            "block-cursor-background": p["surface"], "block-cursor-foreground": p["info"],
            "block-cursor-text-style": "bold", "block-hover-background": p["panel"],
        },
    )


def _rich_theme(p: dict[str, str]) -> RichTheme:
    """Markdown colours for replies (headings, inline code, links, bullets) in the palette."""
    return RichTheme({
        "markdown.h1": f"bold {p['accent']}", "markdown.h1.border": p["border"],
        "markdown.h2": f"bold {p['text']}", "markdown.h3": f"bold {p['text']}",
        "markdown.h4": f"bold {p['muted']}", "markdown.code": f"{p['rose']}",
        "markdown.code_block": f"on {p['panel']}", "markdown.link": p["info"],
        "markdown.link_url": f"underline {p['info']}", "markdown.item.bullet": p["accent"],
        "markdown.item.number": p["accent"], "markdown.block_quote": p["muted"],
        "markdown.hr": p["border"], "markdown.strong": f"bold {p['bright']}",
        "markdown.em": f"italic {p['text']}", "markdown.table.border": p["border"],
    }, inherit=True)


def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds}s" if seconds < 60 else f"{seconds // 60}m {seconds % 60:02d}s"


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


def _tool_title(tool: str, arguments: dict[str, Any]) -> str:
    name = TOOL_TITLES.get(tool, tool.replace("_", " ").title())
    target = _tool_target(arguments)
    return f"{name}({_short(target, 80)})" if target else name


# --------------------------------------------------------------------------- widgets


class CommandInput(Input):
    """Prompt line with persistent ↑/↓ history and slash-command menu navigation."""

    BINDINGS = [
        Binding("up", "history_prev", "History", show=False),
        Binding("down", "history_next", "History", show=False),
        Binding("tab", "complete", "Complete", show=False, priority=True),
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

    def _menu(self) -> "CommandMenu | None":
        menu = getattr(self.app, "command_menu", None)
        return menu if menu is not None and menu.display else None

    def action_history_prev(self) -> None:
        menu = self._menu()
        if menu is not None:
            menu.action_cursor_up()
            return
        if not self.history or self._cursor == 0:
            return
        if self._cursor == len(self.history):
            self._draft = self.value
        self._cursor -= 1
        self.value = self.history[self._cursor]
        self.cursor_position = len(self.value)

    def action_history_next(self) -> None:
        menu = self._menu()
        if menu is not None:
            menu.action_cursor_down()
            return
        if self._cursor >= len(self.history):
            return
        self._cursor += 1
        self.value = self._draft if self._cursor == len(self.history) else self.history[self._cursor]
        self.cursor_position = len(self.value)

    def action_complete(self) -> None:
        menu = self._menu()
        if menu is not None:
            self.app.accept_menu(submit=False)  # type: ignore[attr-defined]
        elif self._suggestion:
            self.value = self._suggestion
            self.cursor_position = len(self.value)


class CommandMenu(OptionList):
    """Slash-command dropdown under the composer."""

    can_focus = False


class SceneView(Static):
    """The animated seasonal scene. One timer drives animation and the 10-minute variation change."""

    def __init__(self, scene: Scene, palette_getter, on_change, **kwargs: Any) -> None:
        super().__init__("", **kwargs)
        self.scene = scene
        self._palette = palette_getter
        self._on_change = on_change
        self._timer = None
        self._last_key: tuple[str, str] | None = None
        self.frame_ms = 0.0
        self.frames = 0

    def on_mount(self) -> None:
        if self._timer is None:  # never start a second animation timer
            self._timer = self.set_interval(1 / SCENE_FPS, self.tick)

    def on_unmount(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    def tick(self) -> None:
        if not self.display or self.size.width < 10:
            return
        started = time.perf_counter()
        self.scene.update()
        self.update(self.scene.render(self.size.width, base=self._palette()["bg"]))
        variation = self.scene.variation
        if variation is not None and variation.key != self._last_key:
            self._last_key = variation.key
            self._on_change()
        elapsed = (time.perf_counter() - started) * 1000
        self.frames += 1
        self.frame_ms = elapsed if self.frames == 1 else self.frame_ms * 0.9 + elapsed * 0.1


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
                ("● ", f"bold {p['amber']}"), ("Permission required  ", f"bold {p['text']}"),
                (_tool_title(tool, self.event.get("arguments") or {}), p["info"]),
            ), id="approval-title")
            yield Static(Text(_short(reason, 300), style=p["muted"]), id="approval-reason")
            with VerticalScroll(id="approval-body"):
                yield Static(Text("ARGUMENTS", style=f"bold {p['muted']}"), classes="approval-label")
                yield Static(Syntax(arguments[:4000], "json", theme=p["code"], background_color=p["panel"],
                                    word_wrap=True), id="approval-arguments")
                if preview:
                    yield Static(Text("PREVIEW", style=f"bold {p['muted']}"), classes="approval-label")
                    lexer = "diff" if _looks_like_diff(preview) else "markdown"
                    yield Static(Syntax(preview[:8000], lexer, theme=p["code"], background_color=p["panel"],
                                        word_wrap=True), id="approval-preview")
            with Horizontal(id="approval-actions"):
                yield Button(Text("Yes, once  [Y]"), id="allow")
                yield Button(Text("Always  [A]"), id="always")
                yield Button(Text("No  [N]"), id="deny")

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
    TITLE = PRODUCT
    SUB_TITLE = f"v{FRIDAY_VERSION}"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding("ctrl+c", "interrupt", "Interrupt / quit", priority=True),
        Binding("escape", "escape", "Stop / close menu", priority=True),
        Binding("ctrl+q", "quit_session", "Quit", priority=True),
        Binding("ctrl+l", "clear_transcript", "Clear", priority=True),
        Binding("ctrl+r", "retry", "Retry", priority=True),
        Binding("ctrl+y", "copy_last", "Copy", priority=True),
        Binding("f2", "toggle_panel", "Panel", priority=True),
        Binding("f3", "toggle_scene", "Scene", priority=True),
        Binding("ctrl+p", "toggle_plan", "Plan", priority=True),
        Binding("pageup", "scroll_transcript(-1)", show=False),
        Binding("pagedown", "scroll_transcript(1)", show=False),
    ]
    CSS = """
    Screen { background: $background; color: $foreground; layers: base overlay; }

    #header { height: 1; padding: 0 2; background: $background; }
    .rule { height: 1; padding: 0 2; background: $background; color: $fx-rule; }
    #scene { height: 10; margin: 0 2; background: $background; }

    #work-area { height: 1fr; width: 100%; }
    #main { width: 1fr; min-width: 40; height: 1fr; }
    #transcript {
        height: 1fr; margin: 0 0 0 1; padding: 0 1;
        background: $background; color: $foreground; border: none;
        scrollbar-size-vertical: 1;
    }
    #stream { height: auto; max-height: 16; margin: 0 1 0 2; padding: 0 1; color: $foreground; }

    #side {
        width: 38; height: 1fr; margin: 0 1 0 1; padding: 0 1;
        background: $background; border-left: vkey $fx-border;
        scrollbar-size-vertical: 1;
    }
    #side Static { height: auto; }
    .side-block { margin-bottom: 1; }
    #guide-title { color: $fx-muted; text-style: bold; }

    #thinking { height: 1; margin: 0 2; padding: 0 1; }
    #composer { height: 3; margin: 0 1; padding: 0 1; border: round $fx-border; background: $background; }
    #composer.-focused { border: round $fx-muted; }
    #composer.-busy { border: round $fx-dim; }
    #prompt-glyph { width: 2; height: 1; color: $fx-muted; text-style: bold; }
    #prompt { height: 1; border: none; padding: 0; background: $background; color: $foreground; }
    #prompt:focus { border: none; background: $background; background-tint: $background 0%; }
    #prompt > .input--placeholder { color: $fx-dim; }
    #prompt > .input--suggestion { color: $fx-dim; }

    #menu {
        height: auto; max-height: 9; margin: 0 2; padding: 0 1;
        background: $background; border: none; display: none;
    }
    #menu > .option-list--option { padding: 0 1; }
    #menu > .option-list--option-highlighted { background: $surface; color: $secondary; text-style: bold; }

    #status { height: 1; padding: 0 2; background: $background; color: $fx-muted; }

    ApprovalScreen { align: center middle; background: $background 70%; }
    #approval-dialog {
        width: 96; max-width: 94%; height: auto; max-height: 88%;
        padding: 1 2; background: $surface; border: round $warning 70%;
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
    Button:hover, Button:focus { background: $fx-border; text-style: bold; }
    Button#allow { color: $success; }
    Button#always { color: $secondary; }
    Button#deny { color: $error; }
    """

    def __init__(self, api: str | None = None, theme: str | None = None):
        super().__init__()
        self.api = (api or os.getenv("FRIDAY_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        selected = theme or os.getenv("FRIDAY_THEME", DEFAULT_THEME)
        self.theme_name = selected if selected in THEMES else DEFAULT_THEME
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
        self.tool_routing = os.getenv("FRIDAY_TOOL_ROUTING", "auto")
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
        self.scene_visible = os.getenv("FRIDAY_SCENE", "1").lower() not in {"0", "off", "false", "no"}
        self.show_stats = os.getenv("FRIDAY_STATS", "").lower() in {"1", "on", "true", "yes"}
        self.output = ""
        self.last_reply = ""
        self._last_stream_render = 0.0
        self.scene = Scene()
        self.command_menu: CommandMenu | None = None
        self._menu_dismissed_for = ""

        # per-turn state and request lifecycle
        # idle | submitted | processing | tool | generating | awaiting_approval | completed | failed | cancelled
        self.request_state = "idle"
        self.current_task = ""
        self.turn_started_at: float | None = None
        self.turn_tools: list[dict[str, Any]] = []
        self.turn_files: list[str] = []
        self.turn_errors = 0
        self.turn_error_message = ""
        self.turn_tokens = 0
        self.turn_route = ""
        self.turn_metrics: dict[str, Any] = {}
        self.last_metrics: dict[str, Any] = {}
        self.last_failed_prompt = ""
        self._last_event_at = 0.0
        self._cancel_reason = ""
        self._status_note = ""
        self._active_tool = ""
        self.todos: list[dict[str, str]] = []
        self.undo_count = 0
        self.usage = {"in": 0, "out": 0}
        self.context_tokens = 0
        self.context_messages = 0
        self._pending_tools: dict[str, dict[str, Any]] = {}
        self._approval_screen: ApprovalScreen | None = None

        # thinking indicator
        self.thinking_started_at: float | None = None
        self.thinking_frame = 0
        self.thinking_timer: Any | None = None

    # ------------------------------------------------------------------ layout

    @property
    def palette(self) -> dict[str, str]:
        return THEMES[self.theme_name]

    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        yield Static("", id="rule-top", classes="rule")
        yield SceneView(self.scene, lambda: self.palette, self._render_scene_caption, id="scene")
        yield Static("", id="rule-scene", classes="rule")
        with Horizontal(id="work-area"):
            with Vertical(id="main"):
                yield RichLog(id="transcript", wrap=True, markup=False, highlight=False, max_lines=6000)
                yield Static("", id="stream")
            with VerticalScroll(id="side"):
                yield Static("", id="side-task", classes="side-block")
                yield Static("", id="side-todos", classes="side-block")
                yield Static("", id="side-tools", classes="side-block")
                yield Static("", id="side-context", classes="side-block")
                yield Static("", id="side-git", classes="side-block")
                yield Static("COMMANDS", id="guide-title")
                yield Static("", id="guide-content")
        yield Static("", id="thinking")
        with Horizontal(id="composer"):
            yield Static("❯", id="prompt-glyph")
            yield CommandInput(
                placeholder="Ask Friday to explain, edit, run or test this project · / for commands",
                id="prompt", max_length=4000,
                suggester=SuggestFromList(self._command_names(), case_sensitive=False),
            )
        menu = CommandMenu(id="menu")
        self.command_menu = menu
        yield menu
        yield Static("", id="status")

    async def on_mount(self) -> None:
        self.console.push_theme(_rich_theme(self.palette))
        self.side_visible = self.size.width >= SIDE_MIN_WIDTH
        self._render_all()
        self._apply_layout(self.size.width)
        self.set_interval(1.0, self._tick)
        self.query_one("#prompt", Input).focus()
        self._write_welcome()

        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))
        self.run_worker(self._refresh_git(), group="git", exclusive=True, exit_on_error=False)
        try:
            # tool_routing "auto": tools are sent only when a prompt needs the workspace (much faster
            # with small local models); warm_up: load the model and cache the system prompt now.
            session = await self._json("POST", "/api/agent/sessions",
                                       {"tool_routing": self.tool_routing if self.tool_routing in
                                        {"auto", "always", "never"} else "auto", "warm_up": True})
            self.session_id = session["id"]
            self.mode = session.get("mode", self.mode)
            self.model = session.get("model") or self.model
            self.workspace = session.get("workspace") or self.workspace
            self.tool_routing = session.get("tool_routing") or self.tool_routing
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
            if session.get("model_note"):
                self._write_warn(str(session["model_note"]))
            self._write_system(f"session {self.session_id[:8]} · {self.model} · {self.workspace} · mode {self.mode}"
                               f" · tools {self.tool_routing}")
            self.run_worker(self._check_model(), group="model", exit_on_error=False)
            self.run_worker(self._refresh_context(), group="context", exclusive=True, exit_on_error=False)
        except Exception as exc:
            self.link = "offline"
            self._write_error(f"Cannot reach the FRIDAY agent API at {self.api}: {exc}",
                              hint="Start FRIDAY (friday start) and relaunch this console.")
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
        show_scene = self.scene_visible and self.size.height >= SCENE_MIN_HEIGHT
        self.query_one("#scene").display = show_scene
        self.query_one("#rule-scene").display = show_scene
        self._render_hud()
        self._render_status()
        self._render_scene_caption()

    def on_descendant_focus(self, event: Any) -> None:
        self.query_one("#composer").set_class(self.focused is self.query_one("#prompt"), "-focused")

    def on_descendant_blur(self, event: Any) -> None:
        self.query_one("#composer").set_class(False, "-focused")

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
            self.link = "degraded"
            self._write_warn("No local models found. Is Ollama running? (ollama serve, then ollama pull llama3.2)")
            self._render_hud()
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
        return time.strftime("%H:%M")

    def _bullet(self, glyph_style: str, body: Any, glyph: str = "●") -> Table:
        grid = Table.grid(padding=0)
        grid.add_column(width=2, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text(glyph, style=glyph_style), body)
        return grid

    def _result_line(self, body: Text) -> Table:
        grid = Table.grid(padding=0)
        grid.add_column(width=5, no_wrap=True)
        grid.add_column(ratio=1)
        grid.add_row(Text("  ⎿  ", style=self.palette["dim"]), body)
        return grid

    def _write_welcome(self) -> None:
        p = self.palette
        width = self.size.width or 100
        if width >= 84 and os.getenv("FRIDAY_NO_BANNER", "") != "1":
            self._write(Padding(logo(["FRIDAY", "CODE"], color=p["accent"], shadow=_mix(p["accent"], p["bg"], 0.55),
                                     base=None), (1, 0, 0, 1)))
        self._write(Text.assemble(("✻ ", f"bold {p['accent']}"), ("Welcome to ", p["rose"]),
                                  (PRODUCT, f"bold {p['rose']}"), (f" v{FRIDAY_VERSION}", p["muted"])))
        self._write(Text.assemble(
            ("  ", ""), ("/help", p["text"]), (" for commands · ", p["muted"]), ("esc", p["text"]),
            (" to stop · ", p["muted"]), ("ctrl+r", p["text"]), (" to retry · ", p["muted"]),
            ("ctrl+y", p["text"]), (" to copy · ", p["muted"]), ("F3", p["text"]), (" scene", p["muted"]),
        ))
        self._write(Text(""))

    def _write_system(self, text: str) -> None:
        p = self.palette
        self._write(Text.assemble(("  · ", p["dim"]), (text, p["muted"])))

    def _write_warn(self, text: str) -> None:
        p = self.palette
        self._write(self._bullet(f"bold {p['amber']}", Text(text, style=p["amber"])))

    def _write_error(self, text: str, hint: str | None = None) -> None:
        p = self.palette
        self._write(self._bullet(f"bold {p['red']}", Text.assemble(("Error  ", f"bold {p['red']}"), (text, p["text"]))))
        if hint:
            self._write(self._result_line(Text(hint, style=p["muted"])))

    def _write_user(self, prompt: str) -> None:
        p = self.palette
        self._write(Text(""))
        body = Text.assemble(("❯ ", f"bold {p['rose']}"), (prompt, p["bright"]))
        self._write(Padding(body, (0, 1), style=f"on {p['user_bg']}", expand=True))
        self._write(Text(""))

    def _reply_renderable(self, text: str) -> Any:
        p = self.palette
        return Markdown(text, code_theme=p["code"], inline_code_theme=p["code"], hyperlinks=False)

    def _write_agent(self, text: str) -> None:
        p = self.palette
        self.last_reply = text
        self._write(self._bullet(f"bold {p['text']}", self._reply_renderable(text)))

    def _write_tool_start(self, event: dict[str, Any]) -> None:
        p = self.palette
        args = event.get("arguments") or {}
        line = Text(_tool_title(str(event.get("tool", "?")), args), style=f"bold {p['text']}")
        line.no_wrap, line.overflow = True, "ellipsis"
        self._write(self._bullet(p["info"], line))

    def _write_tool_result(self, event: dict[str, Any], elapsed: float | None) -> None:
        p = self.palette
        result = str(event.get("result") or "")
        failed = result.startswith(FAIL_PREFIXES)
        lines = result.count("\n") + 1 if result else 0
        timing = f" · {elapsed:.1f}s" if elapsed is not None else ""
        if failed:
            body = Text(_short(result, 160), style=p["red"])
        else:
            first = next((ln for ln in result.splitlines() if ln.strip()), "")
            body = Text(f"{lines} line{'s' if lines != 1 else ''}" + (f" · {_short(first, 80)}" if first else "")
                        + timing, style=p["muted"])
        body.no_wrap, body.overflow = True, "ellipsis"
        self._write(self._result_line(body))
        if not failed and _looks_like_diff(result):
            diff = "\n".join(result.splitlines()[:60])
            self._write(Padding(Syntax(diff, "diff", theme=p["code"], background_color=p["panel"], word_wrap=True),
                                (0, 0, 0, 5)))

    def _write_turn_summary(self, outcome: str) -> None:
        p = self.palette
        elapsed = time.perf_counter() - self.turn_started_at if self.turn_started_at else 0.0
        m = self.turn_metrics
        if outcome == "cancelled":
            self._write(self._result_line(Text.assemble(
                ("Interrupted", f"bold {p['amber']}"),
                (" · partial answer kept · what should Friday do instead?" if self.turn_tokens else
                 " · what should Friday do instead?", p["muted"]))))
            return
        if outcome == "failed":
            return  # the error line already explains what happened
        parts: list[tuple[str, str]] = [("✻ ", p["accent"]), (f"{elapsed:.1f}s", p["muted"])]
        if m.get("ttft_s") is not None:
            parts.append((f" · first word {m['ttft_s']:.1f}s", p["dim"]))
        if m.get("tokens_per_s"):
            parts.append((f" · {m['tokens_per_s']:.1f} tok/s", p["dim"]))
        if self.turn_tools:
            parts.append((f" · {len(self.turn_tools)} tool{'s' if len(self.turn_tools) != 1 else ''}", p["dim"]))
        if self.turn_errors:
            parts.append((f" · {self.turn_errors} failed step{'s' if self.turn_errors != 1 else ''}", p["amber"]))
        if self.turn_files:
            files = ", ".join(dict.fromkeys(self.turn_files))
            parts.append((f" · modified {_short(files, 80)}", p["amber"]))
        if self.undo_count and self.turn_files:
            parts.append((" · /rewind to undo", p["dim"]))
        if self.show_stats and m:
            parts.append((f"\n  prompt {m.get('prompt_tokens', 0)} tok in {m.get('prompt_s', 0):.1f}s · "
                          f"gen {m.get('gen_tokens', 0)} tok in {m.get('gen_s', 0):.1f}s · load {m.get('load_s', 0):.1f}s"
                          f" · tools {m.get('tools')} ({m.get('route', '')}) · steps {m.get('steps')}"
                          f" · retries {m.get('retries')}", p["dim"]))
        self._write(Padding(Text.assemble(*parts), (0, 0, 0, 2)))

    def _clean_reply(self, text: str) -> str:
        """Remove '🐺 BOSS' labels the model adds at the start of any line."""
        return _BOSS_LABEL.sub("", text).lstrip()

    # ------------------------------------------------------------------ chrome rendering

    def _render_all(self) -> None:
        self._render_hud()
        self._render_side()
        self._update_guide()
        self._render_status()
        self._render_scene_caption()

    def _tick(self) -> None:
        self._render_hud()
        if self.busy:
            self._render_side_task()
            self._check_stall()

    def _check_stall(self) -> None:
        """Never leave a question loading forever: fail it if the backend goes silent."""
        if (self.request_state in {"submitted", "processing", "generating", "tool"}
                and self._last_event_at and time.perf_counter() - self._last_event_at > STALL_SECONDS):
            self._cancel_reason = "stalled"
            self.workers.cancel_group(self, "agent")
            self.run_worker(self._cancel_remote(), group="cancel", exit_on_error=False)

    def _render_hud(self) -> None:
        p = self.palette
        width = self.size.width or 120
        link_style = {"online": p["green"], "connecting": p["amber"], "degraded": p["amber"]}.get(self.link, p["red"])
        left = Text.assemble(("✻ ", f"bold {p['accent']}"), (PRODUCT, f"bold {p['text']}"),
                             (f" v{FRIDAY_VERSION}", p["muted"]))
        right = Text.assemble(("● ", link_style), (self.model, p["text"]))
        if self.link not in {"online"}:
            right.append(f" {self.link}", style=link_style)
        if width >= 90:
            right.append(f" · {self.workspace}", style=p["muted"])
        if width >= COMPACT_WIDTH and self.git_state == "repo":
            right.append(f" · ⎇ {self.git_branch}", style=p["info"])
            right.append(f" ±{self.git_changes}" if self.git_changes else " ✓",
                         style=p["amber"] if self.git_changes else p["muted"])
        if self.mode == "plan":
            right.append(" · PLAN", style=f"bold {p['amber']}")
        right.append(f" · {time.strftime('%H:%M')}", style=p["muted"])
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(left, right)
        self.query_one("#header", Static).update(grid)
        self.query_one("#rule-top", Static).update(Text("┈" * max(width - 4, 1), style=p["rule"]))

    def _render_scene_caption(self) -> None:
        p = self.palette
        width = max((self.size.width or 100) - 4, 10)
        caption = f" {self.scene.describe()} · Patch "
        rule = Text("┈" * max(width - len(caption) - 2, 1), style=p["rule"])
        rule.append(caption, style=p["dim"])
        rule.append("┈┈", style=p["rule"])
        try:
            self.query_one("#rule-scene", Static).update(rule)
        except Exception:
            pass

    def _state_label(self) -> Text:
        p = self.palette
        state = self.request_state
        if self.awaiting_approval:
            return Text.assemble(("▲ ", f"bold {p['amber']}"), ("waiting for your approval", p["amber"]))
        labels = {
            "submitted": ("◇ submitted", p["info"]), "processing": ("◆ processing", p["info"]),
            "tool": ("◆ running tool", p["info"]), "generating": ("◆ generating", p["accent"]),
            "completed": ("◆ ready", p["green"]), "failed": ("✕ failed · ^R retry", p["red"]),
            "cancelled": ("◇ cancelled", p["amber"]), "idle": ("◆ ready", p["green"]),
        }
        if self.link == "offline" and not self.busy:
            return Text.assemble(("✕ offline", f"bold {p['red']}"))
        label, style = labels.get(state, ("◆ ready", p["green"]))
        return Text(label, style=style)

    def _render_status(self) -> None:
        p = self.palette
        state = self._state_label()
        state.append_text(Text.assemble(
            ("  ", ""), (self.mode, p["muted"]),
            (" · ", p["dim"]), ("read-only" if self.mode == "plan" else f"net {self.network}", p["muted"]),
            (" · ", p["dim"]), (f"tools {self.tool_routing}", p["muted"]),
        ))
        if self.busy:
            keys = [("esc", "stop")]
        else:
            keys = [("/", "commands")]
            if self.last_failed_prompt:
                keys.append(("^R", "retry"))
            keys += [("^Y", "copy"), ("↑↓", "history"), ("F2", "panel"), ("F3", "scene")]
        if (self.size.width or 120) < COMPACT_WIDTH:
            keys = keys[:3]
        hint = Text()
        for i, (key, label) in enumerate(keys):
            if i:
                hint.append(" · ", style=p["dim"])
            hint.append(key, style=p["text"])
            hint.append(f" {label}", style=p["muted"])
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(justify="right", no_wrap=True)
        grid.add_row(state, hint)
        self.query_one("#status", Static).update(grid)
        self.query_one("#composer").set_class(self.busy, "-busy")

    def _section(self, title: str, body: Text | Group | str) -> Group:
        p = self.palette
        header = Text(title, style=f"bold {p['muted']}")
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
                glyph, style = {"completed": ("☒", p["green"]), "in_progress": ("◐", p["accent"])}.get(
                    status, ("☐", p["muted"]))
                todo.append(f"{glyph} ", style=style)
                todo.append(_short(item.get("content", ""), 30) + "\n",
                            style=p["dim"] if status == "completed" else p["text"])
            todo.rstrip()
        else:
            todo = Text("no task list", style=p["dim"])
        self.query_one("#side-todos", Static).update(self._section("PROGRESS", todo))

        tools = Text()
        if self._pending_tools:
            for info in self._pending_tools.values():
                tools.append("● ", style=p["info"])
                tools.append(TOOL_TITLES.get(info["tool"], info["tool"]) + "\n", style=f"bold {p['info']}")
        recent = self.turn_tools[-6:]
        for info in recent:
            ok = info.get("ok")
            tools.append("✓ " if ok else "✕ " if ok is False else "· ",
                         style=p["green"] if ok else p["red"] if ok is False else p["muted"])
            tools.append(_short(TOOL_TITLES.get(info["tool"], info["tool"]), 16), style=p["text"])
            if info.get("elapsed") is not None:
                tools.append(f"  {info['elapsed']:.1f}s", style=p["muted"])
            tools.append("\n")
        tools.rstrip()
        if not tools.plain:
            tools = Text("idle", style=p["dim"])
        self.query_one("#side-tools", Static).update(self._section("TOOLS", tools))

        ctx = Text()
        if self.num_ctx:
            ratio = min(self.context_tokens / self.num_ctx, 1.0)
            cells = 20
            filled = int(round(ratio * cells))
            bar_style = p["red"] if ratio > 0.85 else p["amber"] if ratio > 0.6 else p["accent"]
            ctx.append("━" * filled, style=bar_style)
            ctx.append("━" * (cells - filled), style=p["border"])
            ctx.append(f" {ratio * 100:.0f}%\n", style=p["text"])
            ctx.append(f"~{_kfmt(self.context_tokens)} / {_kfmt(self.num_ctx)} tokens\n", style=p["muted"])
        ctx.append(f"usage in {_kfmt(self.usage['in'])} · out {_kfmt(self.usage['out'])}\n", style=p["muted"])
        ctx.append(f"{self.context_messages} messages · undo {self.undo_count}", style=p["muted"])
        model = Text.assemble(("\n", ""), (self.model, f"bold {p['text']}"))
        if self.last_metrics.get("tokens_per_s"):
            model.append(f"\nlast: {self.last_metrics['tokens_per_s']:.1f} tok/s · first word "
                         f"{(self.last_metrics.get('ttft_s') or 0):.1f}s", style=p["muted"])
        if self.max_steps:
            model.append(f"\nmax steps {self.max_steps}", style=p["dim"])
        self.query_one("#side-context", Static).update(self._section("CONTEXT · MODEL", Group(ctx, model)))

        if self.git_state == "repo":
            git = Text.assemble(("⎇ ", p["info"]), (self.git_branch, f"bold {p['info']}"), ("\n", ""))
            git.append(f"{self.git_changes} changed file{'s' if self.git_changes != 1 else ''}" if self.git_changes
                       else "working tree clean", style=p["amber"] if self.git_changes else p["muted"])
        elif self.git_state == "none":
            git = Text("not a git repository", style=p["dim"])
        else:
            git = Text("checking…", style=p["dim"])
        self.query_one("#side-git", Static).update(self._section("GIT", git))

    def _render_side_task(self) -> None:
        p = self.palette
        if self.current_task:
            body = Text(_short(self.current_task, 110), style=p["text"])
            if self.busy and self.turn_started_at:
                body.append(f"\nrunning {_clock(time.perf_counter() - self.turn_started_at)}", style=p["info"])
            elif self.turn_started_at is None:
                body.append("\nlast task", style=p["dim"])
        else:
            body = Text("waiting for instructions", style=p["dim"])
        self.query_one("#side-task", Static).update(self._section("TASK", body))

    def _command_names(self) -> list[str]:
        names = [cmd.split()[0] for _, cmd, _ in BUILTIN_COMMANDS]
        names += ["/quit"] + [f"/theme {name}" for name in THEMES]
        names += ["/tools auto", "/tools on", "/tools off", "/scene next", "/scene off", "/scene on", "/copy all"]
        names += [f"/{name}" for name in sorted(self.custom_commands)]
        return names

    def _menu_entries(self) -> list[tuple[str, str]]:
        entries = [(cmd, desc) for _, cmd, desc in BUILTIN_COMMANDS]
        for name, prompt in sorted(self.custom_commands.items()):
            summary = prompt.strip().splitlines()[0] if prompt.strip() else "custom prompt"
            entries.append((f"/{name}", _short(summary, 60)))
        return entries

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
            text.append(cmd + "\n", style=p["info"])
            text.append(f"  {desc}\n", style=p["muted"])
        if self.custom_commands:
            text.append("\nSHORTCUTS\n", style=f"bold {p['muted']}")
            for name, prompt in sorted(self.custom_commands.items()):
                summary = prompt.strip().splitlines()[0] if prompt.strip() else "custom prompt"
                text.append(f"/{name}\n", style=p["info"])
                text.append(f"  {_short(summary, 48)}\n", style=p["muted"])
        text.append("\nKEYS\n", style=f"bold {p['muted']}")
        for key, desc in [("Enter", "send"), ("Esc", "stop generating · close menu"),
                          ("Ctrl+C", "stop / quit"), ("Ctrl+Q", "quit"), ("Ctrl+R", "retry failed question"),
                          ("Ctrl+Y", "copy last code block"), ("↑ / ↓", "prompt history"),
                          ("Tab / →", "complete command"), ("Ctrl+P", "plan mode"), ("F2", "side panel"),
                          ("F3", "scene"), ("PgUp/PgDn", "scroll transcript"),
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

    # ------------------------------------------------------------------ slash-command menu

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "prompt" or self.command_menu is None:
            return
        value = event.value
        menu = self.command_menu
        if value.startswith("/") and " " not in value and value != self._menu_dismissed_for:
            needle = value.lower()
            matches = [(cmd, desc) for cmd, desc in self._menu_entries() if cmd.lower().startswith(needle)]
            if matches and not (len(matches) == 1 and matches[0][0].split()[0] == value):
                p = self.palette
                menu.clear_options()
                menu.add_options([
                    Option(Text.assemble((f"{cmd:<24}", p["text"]), (desc, p["muted"])), id=cmd)
                    for cmd, desc in matches[:24]
                ])
                menu.highlighted = 0
                menu.display = True
                return
        menu.display = False
        if not value.startswith("/"):
            self._menu_dismissed_for = ""

    def accept_menu(self, submit: bool) -> bool:
        """Put the highlighted command into the prompt; submit it right away when it needs no arguments."""
        menu = self.command_menu
        if menu is None or not menu.display or menu.highlighted is None:
            return False
        option = menu.get_option_at_index(menu.highlighted)
        spec = option.id or ""
        command = spec.split()[0]
        needs_args = "<" in spec
        prompt = self.query_one("#prompt", CommandInput)
        menu.display = False
        self._menu_dismissed_for = command
        if submit and not needs_args:
            prompt.value = ""
            prompt.remember(command)
            self._submit(command)
        else:
            prompt.value = command + (" " if needs_args or " " in spec else "")
            prompt.cursor_position = len(prompt.value)
        return True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        if event.option_list.id == "menu":
            self.accept_menu(submit=True)
            self.query_one("#prompt", Input).focus()

    # ------------------------------------------------------------------ thinking indicator

    def _start_thinking(self) -> None:
        if self.thinking_timer is not None:
            self.thinking_timer.stop()
        self.thinking_started_at = time.perf_counter()
        self.thinking_frame = 0
        self.query_one("#stream", Static).update("")
        self._render_thinking()
        self.thinking_timer = self.set_interval(0.12, self._tick_thinking)

    def _tick_thinking(self) -> None:
        if self.thinking_started_at is None:
            return
        self.thinking_frame = (self.thinking_frame + 1) % len(SPINNER)
        self._render_thinking()

    def _stop_thinking(self) -> None:
        if self.thinking_timer is not None:
            self.thinking_timer.stop()
            self.thinking_timer = None
        self.thinking_started_at = None
        self.query_one("#thinking", Static).update("")

    _reset_thinking = _stop_thinking  # compatibility with the previous console

    def _render_thinking(self) -> None:
        p = self.palette
        if self.thinking_started_at is None:
            return
        elapsed = time.perf_counter() - self.thinking_started_at
        glyph = SPINNER[self.thinking_frame]
        state = self.request_state
        if self.awaiting_approval:
            phrase, detail = "Waiting for your approval", ""
        elif self._status_note:
            phrase, detail = self._status_note[:1].upper() + self._status_note[1:], ""
        elif state == "tool":
            phrase, detail = f"Running {self._active_tool}", ""
        elif state == "generating":
            phrase, detail = "Writing", f" · {self.turn_tokens} tokens"
        elif state == "submitted":
            phrase, detail = "Sending", ""
        else:
            pool = READING_PHRASES if self.turn_route not in {"", "none"} else THINKING_PHRASES
            phrase, detail = pool[int(elapsed // 3) % len(pool)], ""
        text = Text.assemble((glyph + " ", f"bold {p['accent']}"), (f"{phrase}…", p["accent"]),
                             (f" ({_clock(elapsed)}{detail} · ", p["muted"]), ("esc", p["text"]),
                             (" to interrupt)", p["muted"]))
        text.no_wrap, text.overflow = True, "ellipsis"
        self.query_one("#thinking", Static).update(text)

    # ------------------------------------------------------------------ input & commands

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self.command_menu is not None and self.command_menu.display:
            if self.accept_menu(submit=True):
                return
        prompt = event.value.strip()
        if not prompt or self.busy:
            return
        event.input.value = ""
        if isinstance(event.input, CommandInput):
            event.input.remember(prompt)
        self._submit(prompt)

    def _submit(self, prompt: str) -> None:
        if self.busy:
            return
        self.busy = True  # set now so a second Enter cannot start another turn
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
            if self.request_state in {"submitted", "processing", "tool", "generating"}:
                self.request_state = "idle"
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
        elif name == "tools":
            mapping = {"auto": "auto", "on": "always", "always": "always", "off": "never", "never": "never"}
            if arg not in mapping:
                self._write_system(f"tools {self.tool_routing} · /tools auto (send tools only when a prompt needs "
                                   "the workspace) · /tools on (every step) · /tools off (chat only)")
                return
            result = await self._json("PATCH", self._session_path, {"tool_routing": mapping[arg]})
            self.tool_routing = result.get("tool_routing", mapping[arg])
            self._write_system(f"tools {self.tool_routing}")
        elif name == "retry":
            if not self.last_failed_prompt:
                self._write_system("nothing to retry")
                return
            await self._run_turn(self.last_failed_prompt)
        elif name == "copy":
            self._copy(everything=arg == "all")
        elif name == "stats":
            self.show_stats = not self.show_stats if not arg else arg in {"on", "1", "true"}
            m = self.last_metrics
            if m:
                self._write_system(
                    f"last answer: {m.get('total_s', 0):.1f}s total · first word {m.get('ttft_s') or 0:.1f}s · "
                    f"{m.get('tokens_per_s') or 0:.1f} tok/s · prompt {m.get('prompt_tokens', 0)} tok "
                    f"({m.get('prompt_s', 0):.1f}s) · generated {m.get('gen_tokens', 0)} tok · model load "
                    f"{m.get('load_s', 0):.1f}s · tools {m.get('tools')} · tool time {m.get('tool_s', 0):.1f}s · "
                    f"retries {m.get('retries', 0)}")
            self._write_system(f"detailed timings {'on' if self.show_stats else 'off'}")
        elif name == "scene":
            self._scene_command(arg)
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
                self.console.push_theme(_rich_theme(self.palette))
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

    def _scene_command(self, arg: str) -> None:
        scene = self.scene
        arg = arg.lower()
        if arg in {"", "status"}:
            v = scene.variation
            self._write_system(f"scene {'on' if self.scene_visible else 'off'} · {scene.describe()} · "
                               f"mode {scene.mode} · next change in "
                               f"{max(0, int(scene.next_change - scene.monotonic())) // 60} min"
                               + (f" · outfit: {self._outfit_label()}" if v else ""))
            return
        if arg in {"on", "off"}:
            self.scene_visible = arg == "on"
            self._apply_layout(self.size.width)
        elif arg == "next":
            scene.change()
        elif arg in {"cycle", "calendar"}:
            scene.mode = arg
            scene.season_override = None
            scene.change()
        elif arg in SEASONS:
            scene.season_override = arg
            scene.change()
        elif arg in WEATHERS:
            scene.weather_override = arg
            scene.change()
            scene.weather_override = None  # one-off preview; the 10-minute change resumes normally
        elif arg in {"day", "night", "dusk"}:
            scene.daylight_override = {"day": 1.0, "night": 0.0, "dusk": 0.45}[arg]
        elif arg == "auto":
            scene.daylight_override = None
            scene.season_override = None
            scene.weather_override = None
            scene.mode = "calendar"
            scene.change()
        else:
            self._write_system("scene options: on · off · next · cycle · calendar · auto · "
                               + " · ".join(SEASONS) + " · " + " · ".join(WEATHERS) + " · day · night · dusk")
            return
        self._render_scene_caption()
        self._write_system(f"scene {scene.describe()}")

    def _outfit_label(self) -> str:
        from .scene import outfit_for

        v = self.scene.variation
        return outfit_for(v.season, v.weather, self.scene.light() < 0.25).label if v else ""

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
            self._write_warn("plan mode on · read-only: Friday will inspect and propose, not change files")
        else:
            self._write_system("plan mode off")
        self._render_all()

    # ------------------------------------------------------------------ agent turn

    def _set_state(self, state: str) -> None:
        if state != self.request_state:
            self.request_state = state
            self._render_status()
            self._render_thinking()

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
        self.turn_error_message, self.turn_tokens, self.turn_route = "", 0, ""
        self.turn_metrics, self._status_note, self._active_tool = {}, "", ""
        self._cancel_reason = ""
        self._last_event_at = time.perf_counter()
        self._pending_tools.clear()
        self._set_state("submitted")
        self.scene.react("ack")
        self._render_side()
        self._start_thinking()
        outcome = "completed"
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
                self._set_state("processing")
                self.scene.react("attentive")
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        self._last_event_at = time.perf_counter()
                        await self._event(json.loads(line[6:]))
            if self.turn_error_message or self.request_state == "failed":
                outcome = "failed"
            elif self.request_state not in {"completed"}:
                # the stream ended without a "done" event: the backend stopped mid-answer
                outcome = "failed"
                self.turn_error_message = "The answer stream ended unexpectedly. Your question was kept."
                self._flush_reply()
                self._write_error(self.turn_error_message, hint="ctrl+r to retry")
        except asyncio.CancelledError:
            outcome = "cancelled" if self._cancel_reason != "stalled" else "failed"
            raise
        except httpx.HTTPError as exc:
            self.link = "offline"
            outcome = "failed"
            self.turn_error_message = f"Lost connection to the agent API: {exc}"
            self._flush_reply()
            self._write_error(self.turn_error_message, hint="ctrl+r to retry · is the FRIDAY backend running?")
        except Exception as exc:
            outcome = "failed"
            self.turn_error_message = str(exc)
            self._flush_reply()
            self._write_error(str(exc), hint="ctrl+r to retry")
        finally:
            self._stop_thinking()
            self._flush_reply()
            if outcome == "failed" and self._cancel_reason == "stalled":
                self._write_error(f"No response from the FRIDAY backend for {int(STALL_SECONDS)}s, so the request "
                                  "was stopped.", hint="ctrl+r to retry · check that Ollama is running")
            self._write_turn_summary(outcome)
            self._finish_turn(outcome, prompt)

    def _finish_turn(self, outcome: str, prompt: str) -> None:
        self.request_state = outcome
        self.turn_started_at = None
        self._pending_tools.clear()
        self._status_note = ""
        if outcome == "failed":
            self.last_failed_prompt = prompt
            self.scene.react("concerned")
            prompt_input = self.query_one("#prompt", Input)
            if not prompt_input.value.strip():  # give the question back, ready to retry or edit
                prompt_input.value = prompt
                prompt_input.cursor_position = len(prompt)
        else:
            if outcome == "completed":
                self.last_failed_prompt = ""
                self.scene.react("happy")
            else:
                self.scene.react("idle")
        self._render_status()
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
        self.query_one("#stream", Static).update(
            self._bullet(f"bold {self.palette['text']}", self._reply_renderable(text)))

    async def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "turn_start":
            self.mode = event.get("mode", self.mode)
            self.model = event.get("model", self.model)
            self.turn_route = str(event.get("tools", ""))
            self._render_hud()
            self._render_thinking()
        elif kind == "token":
            self._status_note = ""
            self.output += event.get("content", "")
            self.turn_tokens += 1
            self._set_state("generating")
            self._render_stream()
        elif kind == "status":
            self._status_note = str(event.get("message", ""))
            self._render_thinking()
        elif kind == "tool_start":
            # text streamed before a tool call is a narration step: commit it to the transcript,
            # without a function call the model may have printed as JSON at its end
            prose, call = split_trailing_call(self.output)
            if call is not None and call.get("name") == event.get("tool"):
                self.output = prose
            self._flush_reply()
            self._write_tool_start(event)
            self._active_tool = _tool_title(str(event.get("tool", "?")), event.get("arguments") or {})
            self._pending_tools[str(event.get("id"))] = {"tool": event.get("tool", "?"),
                                                        "started": time.perf_counter(),
                                                        "arguments": event.get("arguments") or {}}
            self._set_state("tool")
            self._render_side()
        elif kind == "approval_required":
            await self._ask_approval(event)
        elif kind == "tool_result":
            self.mode = event.get("mode", self.mode)
            if "undo_count" in event:
                self.undo_count = int(event["undo_count"])
            pending = self._pending_tools.pop(str(event.get("id")), None)
            elapsed = time.perf_counter() - pending["started"] if pending else event.get("elapsed")
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
            self._set_state("processing")
            self._render_side()
            self._render_hud()
        elif kind == "todos":
            self.todos = list(event.get("items") or event.get("todos") or [])
            self._render_side()
        elif kind == "metrics":
            self.turn_metrics = dict(event)
            self.last_metrics = dict(event)
        elif kind == "error":
            message = event.get("message", "Agent turn failed.")
            if "ollama" in message.lower() or "connect" in message.lower():
                self.link = "degraded"
            self.turn_error_message = message
            self._flush_reply()
            self._write_error(message, hint="ctrl+r to retry · your question is back in the prompt")
            self._set_state("failed")
        elif kind == "done":
            self.mode = event.get("mode", self.mode)
            if "todos" in event:
                self.todos = list(event.get("todos") or [])
            if "undo_count" in event:
                self.undo_count = int(event["undo_count"])
            if event.get("status") == "failed" or self.turn_error_message:
                self._set_state("failed")
            else:
                if self.link == "degraded":
                    self.link = "online"
                self._set_state("completed")
            self._render_all()

    async def _ask_approval(self, event: dict[str, Any]) -> None:
        self._flush_reply()
        self.awaiting_approval = True
        self._render_status()
        self._render_thinking()
        p = self.palette
        self._write(self._result_line(Text.assemble(("approval requested", f"bold {p['amber']}"))))
        screen = ApprovalScreen(event, self.palette)
        self._approval_screen = screen
        try:
            decision = await self.push_screen_wait(screen) or "deny"
        finally:
            self._approval_screen = None
            self.awaiting_approval = False
            self._last_event_at = time.perf_counter()
            self._render_status()
        await self._json("POST", f"{self._session_path}/approvals/{event['id']}", {"decision": decision})
        label = {"allow": ("allowed once", p["green"]), "always": ("always allowed", p["info"])}.get(
            decision, ("denied", p["red"]))
        self._write(self._result_line(Text(label[0], style=f"bold {label[1]}")))

    # ------------------------------------------------------------------ actions

    async def _cancel_remote(self) -> None:
        """Ask the backend to stop the turn too, so Ollama stops generating immediately."""
        if self.client is None or not self.session_id:
            return
        try:
            await self._json("POST", self._session_path + "/cancel")
        except Exception:
            pass

    def action_interrupt(self) -> None:
        if not self.busy:
            self.exit()
            return
        self._stop_generation()

    def action_escape(self) -> None:
        if self.command_menu is not None and self.command_menu.display:
            self.command_menu.display = False
            self._menu_dismissed_for = self.query_one("#prompt", Input).value
            return
        if self.busy:
            self._stop_generation()

    def _stop_generation(self) -> None:
        if self._approval_screen is not None:
            self._approval_screen.dismiss("deny")
        self._cancel_reason = "user"
        self.run_worker(self._cancel_remote(), group="cancel", exit_on_error=False)
        self.workers.cancel_group(self, "agent")

    def action_retry(self) -> None:
        if self.busy or not self.last_failed_prompt:
            return
        prompt_input = self.query_one("#prompt", Input)
        if prompt_input.value.strip() == self.last_failed_prompt:
            prompt_input.value = ""
        self._submit(self.last_failed_prompt)

    def _copy(self, everything: bool = False) -> None:
        text = self.last_reply
        if not text:
            self._write_system("nothing to copy yet")
            return
        blocks = _CODE_BLOCK.findall(text)
        payload = text if everything or not blocks else blocks[-1].rstrip("\n")
        self.copy_to_clipboard(payload)
        what = "reply" if everything or not blocks else "code block"
        self._write_system(f"copied the last {what} ({payload.count(chr(10)) + 1} lines)")

    def action_copy_last(self) -> None:
        self._copy()

    def action_quit_session(self) -> None:
        self.exit()

    def action_clear_transcript(self) -> None:
        self._log().clear()
        self.query_one("#stream", Static).update("")

    def action_toggle_panel(self) -> None:
        self.side_visible = not self.side_visible
        self._apply_layout(self.size.width)
        if self.side_visible and self.size.width < SIDE_MIN_WIDTH:
            self._write_system(f"side panel needs at least {SIDE_MIN_WIDTH} columns")

    def action_toggle_scene(self) -> None:
        self.scene_visible = not self.scene_visible
        self._apply_layout(self.size.width)
        if self.scene_visible and self.size.height < SCENE_MIN_HEIGHT:
            self._write_system(f"the scene needs at least {SCENE_MIN_HEIGHT} rows")

    def action_toggle_plan(self) -> None:
        if self.busy or not self.session_id:
            return
        self._submit("/plan")

    def action_scroll_transcript(self, direction: int) -> None:
        log = self._log()
        log.scroll_page_down() if direction > 0 else log.scroll_page_up()


def _mix(a: str, b: str, t: float) -> str:
    ca = tuple(int(a.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    cb = tuple(int(b.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    return "#" + "".join(f"{int(x + (y - x) * t):02x}" for x, y in zip(ca, cb))


def main() -> None:
    FridayAgentApp().run()


if __name__ == "__main__":
    main()
