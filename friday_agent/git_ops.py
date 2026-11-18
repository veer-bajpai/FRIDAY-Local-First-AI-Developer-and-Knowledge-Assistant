"""Git tools for the Friday coding agent."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from .tools import Context, Tool, ToolError


async def _run_git(ctx: Context, args: list[str], timeout: float = 120) -> str:
    if not (ctx.root / ".git").exists() and not await _is_git_worktree(ctx.root):
        raise ToolError("This workspace is not a Git repository.")
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            *args,
            cwd=ctx.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ToolError(f"Git command timed out after {int(timeout)}s.")
    except FileNotFoundError as exc:
        raise ToolError("Git is not installed or is not available on PATH.") from exc
    text = out.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        raise ToolError(text or f"Git exited with code {proc.returncode}.")
    return text or "(no output)"


async def _is_git_worktree(root: Path) -> bool:
    try:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--git-dir",
            cwd=root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        return proc.returncode == 0
    except (FileNotFoundError, OSError):
        return False


def _inside(ctx: Context, value: str) -> str:
    if "\n" in value or "\x00" in value:
        raise ToolError("Invalid path argument.")
    ctx.resolve(value)
    return value


def _revision(value: Any) -> str:
    revision = str(value).strip()
    if not revision or revision.startswith("-") or any(ch in revision for ch in "\n\x00;&|"):
        raise ToolError("Invalid Git revision.")
    return revision


async def git_status(arguments: dict[str, Any], ctx: Context) -> str:
    short = arguments.get("short", False)
    return await _run_git(ctx, ["status", "--short" if short else "--branch", "--untracked-files=all"])


async def git_diff(arguments: dict[str, Any], ctx: Context) -> str:
    args = ["diff"]
    staged = arguments.get("staged", False)
    if staged:
        args.append("--cached")
    if arguments.get("base"):
        args.append(_revision(arguments["base"]))
    if arguments.get("path"):
        args.append("--")
        args.append(_inside(ctx, str(arguments["path"])))
    return await _run_git(ctx, args)


async def git_log(arguments: dict[str, Any], ctx: Context) -> str:
    limit = max(1, min(int(arguments.get("limit", 10) or 10), 100))
    return await _run_git(ctx, ["log", f"-{limit}", "--date=short", "--pretty=format:%h %ad %d %s"])


async def git_add(arguments: dict[str, Any], ctx: Context) -> str:
    paths = arguments.get("paths")
    if isinstance(paths, str):
        paths = [paths]
    if not isinstance(paths, list) or not paths:
        raise ToolError("Provide one or more paths to add.")
    clean = [_inside(ctx, str(path)) for path in paths]
    return await _run_git(ctx, ["add", "--", *clean])


async def git_commit(arguments: dict[str, Any], ctx: Context) -> str:
    message = str(arguments.get("message", "")).strip()
    if not message:
        raise ToolError("A commit message is required.")
    if "\n" in message or "\x00" in message:
        raise ToolError("Commit messages must be a single line.")
    return await _run_git(ctx, ["commit", "-m", message])


async def git_branch(arguments: dict[str, Any], ctx: Context) -> str:
    del arguments
    return await _run_git(ctx, ["branch", "--show-current", "--list"])


async def git_show(arguments: dict[str, Any], ctx: Context) -> str:
    revision = _revision(arguments.get("revision", "HEAD"))
    return await _run_git(ctx, ["show", "--stat", "--oneline", revision])


def _obj(properties: dict[str, dict[str, Any]], required: list[str] | None = None) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": required or []}


def git_tools() -> list[Tool]:
    string = {"type": "string"}
    boolean = {"type": "boolean"}
    integer = {"type": "integer"}
    return [
        Tool("git_status", "Show the current Git branch and working-tree status.", _obj({"short": boolean}), git_status, True),
        Tool("git_diff", "Show unstaged or staged changes.", _obj({"staged": boolean, "base": string, "path": string}), git_diff, True),
        Tool("git_log", "Show recent commits.", _obj({"limit": integer}), git_log, True),
        Tool("git_branch", "Show the current and available local branches.", _obj({}), git_branch, True),
        Tool("git_show", "Show a commit summary.", _obj({"revision": string}), git_show, True),
        Tool("git_add", "Stage files for commit. Paths must remain inside the workspace.", _obj({"paths": {"type": "array", "items": string}}, ["paths"]), git_add, False),
        Tool("git_commit", "Create a Git commit with an explicit message.", _obj({"message": string}, ["message"]), git_commit, False),
    ]
