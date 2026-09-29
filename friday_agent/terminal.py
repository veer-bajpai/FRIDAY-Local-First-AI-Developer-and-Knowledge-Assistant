"""Full-screen dark terminal interface for Friday's local coding agent."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, RichLog, Static

THEMES = {
    "midnight": {"bg": "#101214", "panel": "#171a1e", "text": "#c0c6ce", "muted": "#77818b", "accent": "#788a9a"},
    "ember": {"bg": "#151211", "panel": "#1d1917", "text": "#c6bdb8", "muted": "#85766f", "accent": "#9b7768"},
    "forest": {"bg": "#111512", "panel": "#181e19", "text": "#bdc6bf", "muted": "#758278", "accent": "#768c7b"},
}


class ApprovalScreen(ModalScreen[str]):
    BINDINGS = [
        Binding("y", "allow", "Allow", show=False),
        Binding("n", "deny", "Deny", show=False),
        Binding("a", "always", "Always allow", show=False),
        Binding("escape", "deny", "Deny", show=False),
    ]

    def __init__(self, event: dict[str, Any]):
        super().__init__()
        self.event = event

    def compose(self) -> ComposeResult:
        arguments = json.dumps(self.event.get("arguments", {}), ensure_ascii=False)
        preview = self.event.get("preview", "")
        with Vertical(id="approval-dialog"):
            yield Static(f"Permission required: {self.event.get('tool', 'unknown')}", id="approval-title")
            yield Static(arguments[:1000], id="approval-arguments")
            if preview:
                yield Static(preview[:4000], id="approval-preview")
            with Horizontal(id="approval-actions"):
                yield Button("Allow once [y]", id="allow")
                yield Button("Always allow [a]", id="always")
                yield Button("Deny [n]", id="deny")

    def action_allow(self) -> None:
        self.dismiss("allow")

    def action_always(self) -> None:
        self.dismiss("always")

    def action_deny(self) -> None:
        self.dismiss("deny")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id)


class FridayAgentApp(App[None]):
    TITLE = "🐺 BOSS"
    SUB_TITLE = "LOCAL CODING AGENT"
    BINDINGS = [Binding("ctrl+c", "quit_session", "Quit")]
    CSS = """
    Screen { background: #101214; color: #c0c6ce; }
    Header { background: #171a1e; color: #9aa6b1; }
    #brand { height: 3; padding: 1 2; color: #aab4bd; background: #101214; }
    #transcript { height: 1fr; margin: 0 1; padding: 0 1; border: round #343a40; background: #101214; color: #c0c6ce; }
    #stream { height: auto; max-height: 10; margin: 0 2; padding: 0 1; color: #aebbc5; }
    #status { height: 1; padding: 0 2; color: #77818b; background: #171a1e; }
    #prompt { dock: bottom; height: 3; margin: 0 1; border: round #424a52; background: #171a1e; color: #c0c6ce; }
    Footer { background: #171a1e; color: #77818b; }
    ApprovalScreen { align: center middle; background: #000000 65%; }
    #approval-dialog { width: 76; max-width: 90%; height: auto; max-height: 80%; padding: 1 2; background: #1a1e22; border: round #606a73; }
    #approval-title { height: 2; color: #c1a58f; text-style: bold; }
    #approval-arguments, #approval-preview { height: auto; max-height: 18; overflow-y: auto; color: #b4bbc2; }
    #approval-actions { height: 3; align: right middle; }
    Button { margin-left: 1; background: #30373e; color: #c0c6ce; border: none; }
    Button:hover { background: #424a52; }
    """

    def __init__(self, api: str | None = None, theme: str | None = None):
        super().__init__()
        self.api = (api or os.getenv("FRIDAY_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        selected = theme or os.getenv("FRIDAY_THEME", "midnight")
        self.theme_name = selected if selected in THEMES else "midnight"
        self.mode = "default"
        self.session_id = ""
        self.custom_commands: dict[str, str] = {}
        self.client: httpx.AsyncClient | None = None
        self.busy = False
        self.output = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static("🐺  BOSS   /help for commands", id="brand")
        yield RichLog(id="transcript", wrap=True, markup=False, highlight=False, max_lines=4000)
        yield Static("", id="stream")
        yield Static("Connecting to local Friday agent…", id="status")
        yield Input(placeholder="Ask Friday to inspect, edit, and test this project", id="prompt", max_length=4000)
        yield Footer()

    async def on_mount(self) -> None:
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(None, connect=5))
        try:
            session = await self._json("POST", "/api/agent/sessions", {})
            self.session_id = session["id"]
            self.mode = session["mode"]
            self.custom_commands = await self._json("GET", "/api/agent/commands")
            self._write(f"🐺 BOSS  Local coding session · {session['model']} · {session['workspace']}")
            self._update_status()
            self.query_one("#prompt", Input).focus()
        except Exception as exc:
            self._write(f"Connection error: {exc}")
            self.query_one("#status", Static).update("Backend unavailable. Start Friday and try again.")

    async def on_unmount(self) -> None:
        if self.client is not None:
            if self.session_id:
                try:
                    await self._json("DELETE", self._session_path)
                except httpx.HTTPError:
                    pass
            await self.client.aclose()

    @property
    def _session_path(self) -> str:
        return f"/api/agent/sessions/{self.session_id}"

    async def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        if self.client is None:
            raise RuntimeError("The Friday API client has not started.")
        response = await self.client.request(method, self.api + path, json=body)
        if response.is_error:
            try:
                detail = response.json().get("detail", response.text)
            except (ValueError, json.JSONDecodeError):
                detail = response.text
            raise RuntimeError(f"{response.status_code}: {detail}")
        return response.json()

    def _write(self, text: str) -> None:
        self.query_one("#transcript", RichLog).write(text)

    def _update_status(self) -> None:
        theme = THEMES[self.theme_name]
        self.screen.styles.background = theme["bg"]
        for selector in ("Header", "Footer"):
            widget = self.query_one(selector)
            widget.styles.background = theme["panel"]
            widget.styles.color = theme["muted"]
        brand = self.query_one("#brand", Static)
        brand.styles.background = theme["bg"]
        brand.styles.color = theme["accent"]
        transcript = self.query_one("#transcript", RichLog)
        transcript.styles.background = theme["bg"]
        transcript.styles.color = theme["text"]
        stream = self.query_one("#stream", Static)
        stream.styles.background = theme["bg"]
        stream.styles.color = theme["accent"]
        status = self.query_one("#status", Static)
        status.styles.background = theme["panel"]
        status.styles.color = theme["muted"]
        prompt = self.query_one("#prompt", Input)
        prompt.styles.background = theme["panel"]
        prompt.styles.color = theme["text"]
        self.query_one("#status", Static).update(f"{self.theme_name}  ·  {self.mode}  ·  network: ask")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        event.input.value = ""
        if not prompt or self.busy:
            return
        self.run_worker(self._process(prompt), name="friday-agent-turn", group="agent", exclusive=True, exit_on_error=False)

    async def _process(self, prompt: str) -> None:
        self.busy = True
        input_widget = self.query_one("#prompt", Input)
        input_widget.disabled = True
        try:
            if prompt.startswith("/"):
                await self._command(prompt)
            else:
                await self._run_turn(prompt)
        except Exception as exc:
            self._write(f"Error: {exc}")
        finally:
            self.busy = False
            input_widget.disabled = False
            input_widget.focus()

    async def _command(self, line: str) -> None:
        name, _, arg = line[1:].partition(" ")
        arg = arg.strip()
        if name in {"exit", "quit"}:
            self.exit()
        elif name == "help":
            self._write("Commands: /plan /rewind [n] /context /compact /batch <task> /code-review [PR] /security-review [PR] /agents /msg <id> <text> /voice /insights /theme <midnight|ember|forest> /clear /exit")
        elif name == "clear":
            self.query_one("#transcript", RichLog).clear()
        elif name == "plan":
            self.mode = "default" if self.mode == "plan" else "plan"
            result = await self._json("PATCH", self._session_path, {"mode": self.mode})
            self.mode = result["mode"]
            self._write(f"Plan mode {'ON (read-only)' if self.mode == 'plan' else 'OFF'}")
            self._update_status()
        elif name == "rewind":
            count = int(arg or "1")
            result = await self._json("POST", self._session_path + "/rewind", {"count": count})
            self._write("Rewound: " + (", ".join(result["rewound"]) or "nothing"))
        elif name == "context":
            result = await self._json("GET", self._session_path + "/context")
            self._write(f"messages={result['messages']}  estimated context ~{result['estimated_tokens']} tokens  usage in={result['input_tokens']} out={result['output_tokens']}  undo={result['undo_count']}")
        elif name == "compact":
            result = await self._json("POST", self._session_path + "/compact", {})
            self._write(f"History compacted. {result['messages']} messages remain.")
        elif name == "theme":
            if arg not in THEMES:
                self._write("Themes (dark only): " + ", ".join(THEMES))
            else:
                self.theme_name = arg
                self._update_status()
        elif name in {"batch", "code-review", "security-review", "msg"}:
            if name == "batch":
                prompt = f"Split this into independent subtasks touching separate files. Use batch_worktrees to create an isolated branch and worktree for each. Task: {arg}"
            elif name == "msg":
                target, _, text = arg.partition(" ")
                if not target or not text:
                    raise ValueError("Usage: /msg <session-id> <message>")
                prompt = f"Use SendMessage to send this exact message to Friday session {target}: {json.dumps(text)}"
            else:
                review = "Security review for injection, authorization, secrets, path traversal, and unsafe subprocess use." if name == "security-review" else "Code review for correctness, edge cases, missing tests, and regressions."
                prompt = f"{review} Use git_review_diff{' with PR number ' + arg if arg else ' for the working diff'}. Cite file and line references."
            await self._run_turn(prompt)
        elif name == "agents":
            sessions = await self._json("GET", "/api/agent/sessions")
            others = [item for item in sessions if item["id"] != self.session_id[:8]]
            self._write("\n".join(f"{item['id']}  {item['workspace']}  {item['mode']}" for item in others) or "No other agent sessions.")
        elif name == "voice":
            result = await self._json("POST", "/api/agent/voice", {})
            self._write("Heard: " + (result["text"] or "nothing"))
            if result["text"]:
                await self._run_turn(result["text"])
        elif name == "insights":
            result = await self._json("GET", "/api/agent/insights")
            self._write("Insights report opened: " + result["path"])
        elif name in self.custom_commands:
            await self._run_turn(self.custom_commands[name].replace("$ARGUMENTS", arg))
        else:
            raise ValueError(f"Unknown command /{name}. Try /help.")

    async def _run_turn(self, prompt: str) -> None:
        self._write(f"you › {prompt}")
        self.output = ""
        stream = self.query_one("#stream", Static)
        stream.update("")
        async with self.client.stream("POST", self.api + self._session_path + "/turns", json={"prompt": prompt}) as response:
            if response.is_error:
                raise RuntimeError(f"{response.status_code}: {(await response.aread()).decode('utf-8', 'replace')}")
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                await self._event(json.loads(line[6:]))
        if self.output:
            self._write(self.output)
        stream.update("")

    async def _event(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "turn_start":
            self.mode = event.get("mode", self.mode)
            self._update_status()
        elif kind == "token":
            self.output += event.get("content", "")
            self.query_one("#stream", Static).update(self.output)
        elif kind == "tool_start":
            args = event.get("arguments", {})
            detail = args.get("command") or args.get("path") or ""
            self._write(f"[{event.get('tool')}{': ' + detail if detail else ''}]")
        elif kind == "approval_required":
            decision = await self.push_screen_wait(ApprovalScreen(event))
            await self._json("POST", f"{self._session_path}/approvals/{event['id']}", {"decision": decision or "deny"})
        elif kind == "tool_result":
            self.mode = event.get("mode", self.mode)
            result = event.get("result", "")
            if result.startswith(("Tool error:", "Permission denied:", "Blocked by", "User declined")):
                self._write(result)
            self._update_status()
        elif kind == "error":
            self._write("Error: " + event.get("message", "Agent turn failed."))
        elif kind == "done":
            self.mode = event.get("mode", self.mode)
            self._update_status()

    def action_quit_session(self) -> None:
        self.exit()


def main() -> None:
    FridayAgentApp().run()


if __name__ == "__main__":
    main()
