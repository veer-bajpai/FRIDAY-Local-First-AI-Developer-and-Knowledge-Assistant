"""Tools the model can call. Each tool = JSON schema + async function + optional diff preview."""
from __future__ import annotations

import asyncio
import difflib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx

from .config import API_URL

IGNORE_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv", "dist", "build", ".friday", "data"}
MAX_OUT = 20_000

class ToolError(Exception):
    """Raised for expected failures; the message is shown to the model so it can recover."""

@dataclass
class Context:
    root: Path
    read_files: set = field(default_factory=set)
    todos: list = field(default_factory=list)
    checkpoints: list = field(default_factory=list)  # (Path, previous_text | None) for /undo
    subagent_runner: Any = None

    def resolve(self, p: str) -> Path:
        path = Path(p) if os.path.isabs(p) else self.root / p
        path = path.resolve()
        if path != self.root and self.root not in path.parents:
            raise ToolError(f"'{p}' is outside the workspace ({self.root}). Only files inside it are allowed.")
        return path

    def rel(self, p: Path) -> str:
        try:
            return p.relative_to(self.root).as_posix()
        except ValueError:
            return str(p)

@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    run: Callable[[dict, Context], Awaitable[str]]
    read_only: bool = False
    preview: Callable[[dict, Context], str] | None = None

    def schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

def _truncate(s: str, limit: int = MAX_OUT) -> str:
    if len(s) <= limit:
        return s
    half = limit // 2
    return s[:half] + f"\n... [{len(s) - limit} characters truncated] ...\n" + s[-half:]

def _is_binary(p: Path) -> bool:
    try:
        return b"\0" in p.read_bytes()[:4096]
    except OSError:
        return True

def _diff(old: str, new: str, name: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
                                        f"a/{name}", f"b/{name}", n=2))

# ------------------------------------------------------------------ file tools
async def read_file(a: dict, ctx: Context) -> str:
    p = ctx.resolve(a["path"])
    if not p.is_file():
        raise ToolError(f"{a['path']} is not a file (use list_dir / glob to find it).")
    if _is_binary(p):
        raise ToolError(f"{a['path']} looks like a binary file.")
    offset, limit = int(a.get("offset", 0) or 0), int(a.get("limit", 2000) or 2000)
    lines = p.read_text(errors="replace").splitlines()
    ctx.read_files.add(p)
    chunk = lines[offset:offset + limit]
    out = "\n".join(f"{i + offset + 1:>5}\t{ln[:2000]}" for i, ln in enumerate(chunk))
    rest = len(lines) - offset - limit
    if rest > 0:
        out += f"\n... ({rest} more lines; call again with offset={offset + limit})"
    return out or "(empty file)"

def _write_preview(a: dict, ctx: Context) -> str:
    try:
        p = ctx.resolve(a["path"])
        old = p.read_text(errors="replace") if p.exists() else ""
        return _diff(old, a.get("content", ""), ctx.rel(p)) or "(no changes)"
    except Exception:
        return ""

async def write_file(a: dict, ctx: Context) -> str:
    p = ctx.resolve(a["path"])
    existed = p.exists()
    if existed and p not in ctx.read_files:
        raise ToolError(f"{a['path']} already exists. Read it with read_file before overwriting it.")
    old = p.read_text(errors="replace") if existed else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(a["content"], encoding="utf-8")
    ctx.checkpoints.append((p, old))
    ctx.read_files.add(p)
    return f"{'Overwrote' if existed else 'Created'} {ctx.rel(p)} ({len(a['content'].splitlines())} lines)"

def _edit_apply(a: dict, ctx: Context) -> tuple[Path, str, str]:
    p = ctx.resolve(a["path"])
    if not p.is_file():
        raise ToolError(f"{a['path']} does not exist. Use write_file to create new files.")
    old_s, new_s = a["old_string"], a["new_string"]
    if old_s == new_s:
        raise ToolError("old_string and new_string are identical.")
    text = p.read_text(errors="replace")
    n = text.count(old_s)
    if n == 0:
        raise ToolError("old_string was not found. It must match the file exactly, including whitespace. "
                        "Re-read the file and copy the text precisely.")
    if n > 1 and not a.get("replace_all"):
        raise ToolError(f"old_string appears {n} times. Add surrounding context to make it unique, "
                        "or set replace_all=true.")
    return p, text, text.replace(old_s, new_s) if a.get("replace_all") else text.replace(old_s, new_s, 1)

def _edit_preview(a: dict, ctx: Context) -> str:
    try:
        p, old, new = _edit_apply(a, ctx)
        return _diff(old, new, ctx.rel(p))
    except Exception as e:
        return f"(cannot preview: {e})"

async def edit_file(a: dict, ctx: Context) -> str:
    p = ctx.resolve(a["path"])
    if p.is_file() and p not in ctx.read_files:
        raise ToolError(f"Read {a['path']} with read_file before editing it.")
    p, old, new = _edit_apply(a, ctx)
    p.write_text(new, encoding="utf-8")
    ctx.checkpoints.append((p, old))
    d = _diff(old, new, ctx.rel(p))
    added = sum(1 for l in d.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in d.splitlines() if l.startswith("-") and not l.startswith("---"))
    return f"Edited {ctx.rel(p)}: +{added} -{removed}"

async def list_dir(a: dict, ctx: Context) -> str:
    p = ctx.resolve(a.get("path") or ".")
    if not p.is_dir():
        raise ToolError(f"{a.get('path')} is not a directory.")
    rows = []
    for c in sorted(p.iterdir(), key=lambda x: (x.is_file(), x.name.lower())):
        if c.name in IGNORE_DIRS:
            continue
        rows.append(c.name + ("/" if c.is_dir() else ""))
    return "\n".join(rows[:300]) or "(empty)"

async def glob_files(a: dict, ctx: Context) -> str:
    base = ctx.resolve(a.get("path") or ".")
    hits = [p for p in base.glob(a["pattern"])
            if p.is_file() and not (set(p.relative_to(ctx.root).parts) & IGNORE_DIRS)]
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out = "\n".join(ctx.rel(p) for p in hits[:200])
    return out or "No files matched."

async def grep(a: dict, ctx: Context) -> str:
    base = ctx.resolve(a.get("path") or ".")
    pattern, file_glob = a["pattern"], a.get("glob")
    if shutil.which("rg"):
        cmd = ["rg", "-n", "--no-heading", "--max-columns", "300", "-e", pattern]
        if file_glob:
            cmd += ["-g", file_glob]
        cmd.append(str(base))
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=ctx.root, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.STDOUT)
        out, _ = await proc.communicate()
        text = out.decode("utf-8", "replace")
        return _truncate(text, 12_000) if text.strip() else "No matches."
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError(f"Invalid regex: {e}")
    results: list[str] = []
    files = [base] if base.is_file() else (p for p in base.rglob(file_glob or "*") if p.is_file())
    for f in files:
        if set(f.relative_to(ctx.root).parts) & IGNORE_DIRS or _is_binary(f):
            continue
        for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
            if rx.search(line):
                results.append(f"{ctx.rel(f)}:{i}:{line[:300]}")
                if len(results) >= 300:
                    return "\n".join(results) + "\n... (truncated at 300 matches)"
    return "\n".join(results) or "No matches."

# ------------------------------------------------------------------ shell
async def bash(a: dict, ctx: Context) -> str:
    cmd = a["command"]
    timeout = min(int(a.get("timeout", 120) or 120), 600)
    proc = await asyncio.create_subprocess_shell(cmd, cwd=ctx.root, stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT,
                                                 stdin=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        raise ToolError(f"Command timed out after {timeout}s and was killed.")
    except asyncio.CancelledError:
        proc.kill()
        raise
    text = _truncate(out.decode("utf-8", "replace"))
    return f"exit code {proc.returncode}\n{text}".rstrip()

# ------------------------------------------------------------------ planning / memory
async def todo_write(a: dict, ctx: Context) -> str:
    todos = a.get("todos")
    if isinstance(todos, str):
        todos = json.loads(todos)
    clean = []
    for t in todos or []:
        status = t.get("status", "pending")
        if status not in ("pending", "in_progress", "completed"):
            status = "pending"
        clean.append({"content": str(t.get("content", "")).strip(), "status": status})
    ctx.todos = clean
    return "Todo list updated. Keep exactly one item in_progress and mark items completed as you finish."

async def exit_plan_mode(a: dict, ctx: Context) -> str:
    return "The user approved the plan. Start implementing it now."

# ------------------------------------------------------------------ network / knowledge
def _strip_html(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript).*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()

async def web_fetch(a: dict, ctx: Context) -> str:
    url = a["url"]
    if not url.startswith(("http://", "https://")):
        raise ToolError("URL must start with http:// or https://")
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                     headers={"User-Agent": "Friday/1.0"}) as c:
            r = await c.get(url)
    except httpx.HTTPError as e:
        raise ToolError(f"Fetch failed: {e}")
    body = r.text
    if "html" in r.headers.get("content-type", ""):
        body = _strip_html(body)
    return _truncate(f"HTTP {r.status_code}\n{body}", 15_000)

async def search_knowledge(a: dict, ctx: Context) -> str:
    """Query the user's uploaded documents through the existing Friday RAG API."""
    q, k = a["query"], int(a.get("k", 4) or 4)
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            # Both field names are sent so this works whichever your Pydantic model expects.
            r = await c.post(f"{API_URL}/api/search", json={"query": q, "question": q, "k": k})
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise ToolError(f"Friday knowledge API unavailable ({e}). Is the FastAPI backend running?")
    return _truncate(json.dumps(r.json(), indent=1), 10_000)

async def task(a: dict, ctx: Context) -> str:
    if ctx.subagent_runner is None:
        raise ToolError("Subagents are not available here.")
    return await ctx.subagent_runner(a["prompt"])

# ------------------------------------------------------------------ registry
def _obj(props: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": props, "required": required}

_S = {"type": "string"}

def build_tools(read_only: bool = False) -> dict[str, Tool]:
    tools = [
        Tool("read_file", "Read a text file with line numbers. Always read a file before editing it.",
             _obj({"path": _S, "offset": {"type": "integer", "description": "0-based line to start at"},
                   "limit": {"type": "integer", "description": "max lines (default 2000)"}}, ["path"]),
             read_file, True),
        Tool("list_dir", "List files and folders in a directory.",
             _obj({"path": _S}, []), list_dir, True),
        Tool("glob", "Find files by glob pattern, e.g. 'src/**/*.tsx'. Newest first.",
             _obj({"pattern": _S, "path": _S}, ["pattern"]), glob_files, True),
        Tool("grep", "Search file contents with a regex. Returns file:line:text.",
             _obj({"pattern": _S, "path": _S, "glob": {"type": "string", "description": "e.g. '*.py'"}},
                  ["pattern"]), grep, True),
        Tool("search_knowledge", "Semantic/keyword search over the user's uploaded Friday documents.",
             _obj({"query": _S, "k": {"type": "integer"}}, ["query"]), search_knowledge, True),
        Tool("edit_file", "Replace exact text in an existing file. old_string must be unique unless "
                          "replace_all is true. Prefer this over write_file for changes.",
             _obj({"path": _S, "old_string": _S, "new_string": _S,
                   "replace_all": {"type": "boolean"}}, ["path", "old_string", "new_string"]),
             edit_file, False, _edit_preview),
        Tool("write_file", "Create a new file or fully overwrite a file you have already read.",
             _obj({"path": _S, "content": _S}, ["path", "content"]), write_file, False, _write_preview),
        Tool("bash", "Run a shell command in the workspace (tests, builds, git, package managers). "
                     "Non-interactive only.",
             _obj({"command": _S, "timeout": {"type": "integer", "description": "seconds, max 600"}},
                  ["command"]), bash),
        Tool("web_fetch", "Fetch a URL and return its text.", _obj({"url": _S}, ["url"]), web_fetch),
        Tool("todo_write", "Create/update the task list for multi-step work (3+ steps). Send the FULL list.",
             _obj({"todos": {"type": "array", "items": _obj(
                 {"content": _S, "status": {"type": "string",
                                            "enum": ["pending", "in_progress", "completed"]}},
                 ["content", "status"])}}, ["todos"]), todo_write),
        Tool("task", "Delegate a research question to a read-only subagent with a fresh context. "
                     "Use for broad codebase exploration. Returns its report.",
             _obj({"prompt": _S}, ["prompt"]), task, True),
        Tool("exit_plan_mode", "In plan mode only: present your finished plan (markdown) for approval.",
             _obj({"plan": _S}, ["plan"]), exit_plan_mode, False,
             lambda a, ctx: a.get("plan", "")),
    ]
    if read_only:
        tools = [t for t in tools if t.read_only and t.name != "task"]
    return {t.name: t for t in tools}
