"""Permission-gated Git operations exposed to the agent tool loop."""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

from .tools import Context, Tool, ToolError, _obj

_STRING = {"type": "string"}


async def _run(ctx: Context, *args: str, cwd: Path | None = None) -> str:
    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd or ctx.root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        stdin=asyncio.subprocess.DEVNULL,
    )
    output, _ = await process.communicate()
    text = output.decode("utf-8", "replace").strip()
    if process.returncode:
        raise ToolError(f"{' '.join(args)} failed (exit {process.returncode}):\n{text}")
    return text or "(completed successfully)"


async def git_commit(args: dict, ctx: Context) -> str:
    staged = await _run(ctx, "git", "add", "-A")
    committed = await _run(ctx, "git", "commit", "-m", args["message"])
    return f"{staged}\n{committed}"


async def git_branch(args: dict, ctx: Context) -> str:
    name = args["name"].strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,79}", name) or ".." in name:
        raise ToolError("Branch name contains unsupported characters.")
    return await _run(ctx, "git", "switch", "-c", name)


async def create_pr(args: dict, ctx: Context) -> str:
    pushed = await _run(ctx, "git", "push", "-u", "origin", "HEAD")
    created = await _run(ctx, "gh", "pr", "create", "--title", args["title"], "--body", args["body"])
    return f"{pushed}\n{created}"


async def git_review_diff(args: dict, ctx: Context) -> str:
    pr = args.get("pr")
    if pr is not None:
        if not str(pr).isdigit():
            raise ToolError("PR number must be a positive integer.")
        output = await _run(ctx, "gh", "pr", "diff", str(pr))
    else:
        output = await _run(ctx, "git", "diff", "HEAD")
    return output[:60_000] or "No diff to review."


async def batch_worktrees(args: dict, ctx: Context) -> str:
    tasks = args.get("tasks", [])
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
        result = await _run(ctx, "git", "worktree", "add", "-b", branch, str(worktree))
        created.append(f"{branch} ({worktree}): {result}")
    return "Worktrees ready for their independent tasks:\n" + "\n".join(created)


def git_tools() -> list[Tool]:
    return [
        Tool("git_commit", "Stage all project changes and commit with the supplied message.",
             _obj({"message": _STRING}, ["message"]), git_commit),
        Tool("git_branch", "Create and switch to a new local branch.",
             _obj({"name": _STRING}, ["name"]), git_branch),
        Tool("create_pr", "Push the current branch and open a pull request with GitHub CLI.",
             _obj({"title": _STRING, "body": _STRING}, ["title", "body"]), create_pr,
             network=True),
        Tool("git_review_diff", "Return the current working diff or a GitHub pull request diff for review.",
             _obj({"pr": {"type": "integer"}}, []), git_review_diff, read_only=True, network=True),
        Tool("batch_worktrees", "Create isolated branches/worktrees for up to six independent coding tasks.",
             _obj({"tasks": {"type": "array", "items": _STRING}}, ["tasks"]), batch_worktrees),
    ]