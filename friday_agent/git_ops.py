"""Git tools for the Friday coding agent."""
from __future__ import annotations

import asyncio
import re
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
    name = str(arguments.get("name") or "").strip()
    if not name:
        return await _run_git(ctx, ["branch", "--list"])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}", name) or ".." in name or name.endswith((".lock", "/")):
        raise ToolError("Branch name contains unsupported characters.")
    return await _run_git(ctx, ["switch", "-c", name])


async def git_show(arguments: dict[str, Any], ctx: Context) -> str:
    revision = _revision(arguments.get("revision", "HEAD"))
    return await _run_git(ctx, ["show", "--stat", "--oneline", revision])


async def _run_cmd(ctx: Context, *args: str, timeout: float = 300) -> str:
    """Run git or gh with no shell; used by the PR, review and worktree tools."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *args, cwd=ctx.root, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT, stdin=asyncio.subprocess.DEVNULL,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ToolError(f"{args[0]} timed out after {int(timeout)}s.")
    except FileNotFoundError as exc:
        raise ToolError(f"{args[0]} is not installed or is not on PATH.") from exc
    text = out.decode("utf-8", "replace").strip()
    if proc.returncode:
        raise ToolError(f"{' '.join(args[:3])} failed (exit {proc.returncode}):\n{text}")
    return text or "(completed successfully)"


async def create_pr(arguments: dict[str, Any], ctx: Context) -> str:
    pushed = await _run_cmd(ctx, "git", "push", "-u", "origin", "HEAD")
    created = await _run_cmd(ctx, "gh", "pr", "create", "--title", str(arguments["title"]),
                             "--body", str(arguments["body"]))
    return f"{pushed}\n{created}"


async def git_review_diff(arguments: dict[str, Any], ctx: Context) -> str:
    pr = arguments.get("pr")
    if pr is not None:
        if not str(pr).isdigit():
            raise ToolError("PR number must be a positive integer.")
        output = await _run_cmd(ctx, "gh", "pr", "diff", str(pr))
    else:
        output = await _run_git(ctx, ["diff", "HEAD"])
    return output[:60_000] or "No diff to review."


async def batch_worktrees(arguments: dict[str, Any], ctx: Context) -> str:
    tasks = arguments.get("tasks", [])
    if not isinstance(tasks, list) or not 1 <= len(tasks) <= 6:
        raise ToolError("Provide between one and six independent task names.")
    created = []
    for task_name in tasks:
        name = str(task_name).strip()
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+){0,5}", name):
            raise ToolError(f"Invalid worktree task name: {name}")
        worktree = ctx.root.parent / f"{ctx.root.name}-wt-{name}"
        if worktree.exists():
            raise ToolError(f"Worktree path already exists: {worktree}")
        branch = f"friday/{name}"
        result = await _run_git(ctx, ["worktree", "add", "-b", branch, str(worktree)])
        created.append(f"{branch} ({worktree}): {result}")
    return "Worktrees ready for their independent tasks:\n" + "\n".join(created)


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
        Tool("git_branch", "List local branches, or create and switch to a new branch when 'name' is given.", _obj({"name": string}), git_branch, False),
        Tool("git_show", "Show a commit summary.", _obj({"revision": string}), git_show, True),
        Tool("git_add", "Stage files for commit. Paths must remain inside the workspace.", _obj({"paths": {"type": "array", "items": string}}, ["paths"]), git_add, False),
        Tool("git_commit", "Create a Git commit with an explicit message.", _obj({"message": string}, ["message"]), git_commit, False),
        Tool("create_pr", "Push the current branch and open a pull request with GitHub CLI.",
             _obj({"title": string, "body": string}, ["title", "body"]), create_pr, False, network=True),
        Tool("git_review_diff", "Return the working diff or a GitHub pull request diff for review.",
             _obj({"pr": integer}), git_review_diff, True, network=True),
        Tool("batch_worktrees", "Create isolated branches/worktrees for up to six independent coding tasks.",
             _obj({"tasks": {"type": "array", "items": string}}, ["tasks"]), batch_worktrees, False),
    ]
