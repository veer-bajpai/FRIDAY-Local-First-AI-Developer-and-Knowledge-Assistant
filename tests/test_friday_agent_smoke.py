import asyncio
from pathlib import Path

import pytest

from friday_agent.permissions import Permissions
from friday_agent.tools import Context, ToolError, build_tools

def run(coro):
    return asyncio.run(coro)

def test_write_read_edit_roundtrip(tmp_path: Path):
    ctx = Context(root=tmp_path.resolve())
    tools = build_tools()
    run(tools["write_file"].run({"path": "a.txt", "content": "hello\nworld\n"}, ctx))
    out = run(tools["read_file"].run({"path": "a.txt"}, ctx))
    assert "hello" in out
    run(tools["edit_file"].run({"path": "a.txt", "old_string": "world", "new_string": "friday"}, ctx))
    assert (tmp_path / "a.txt").read_text() == "hello\nfriday\n"
    assert len(ctx.checkpoints) == 2

def test_sandbox_blocks_outside_paths(tmp_path: Path):
    ctx = Context(root=tmp_path.resolve())
    with pytest.raises(ToolError):
        ctx.resolve("../../etc/passwd")

def test_edit_requires_prior_read(tmp_path: Path):
    (tmp_path / "b.txt").write_text("x")
    ctx = Context(root=tmp_path.resolve())
    with pytest.raises(ToolError):
        run(build_tools()["edit_file"].run({"path": "b.txt", "old_string": "x", "new_string": "y"}, ctx))

def test_permissions(tmp_path: Path):
    tools = build_tools()
    perms = Permissions(tmp_path.resolve())
    assert perms.check(tools["read_file"], {"path": "a"})[0] == "allow"
    assert perms.check(tools["write_file"], {"path": "a", "content": ""})[0] == "ask"
    assert perms.check(tools["bash"], {"command": "ls -la"})[0] == "allow"
    assert perms.check(tools["bash"], {"command": "ls; rm x"})[0] == "ask"
    assert perms.check(tools["bash"], {"command": "sudo rm -rf /"})[0] == "deny"
    assert perms.check(tools["bash"], {"command": "git push --force"})[0] == "deny"
    perms.mode = "plan"
    assert perms.check(tools["write_file"], {"path": "a", "content": ""})[0] == "deny"
    perms.mode = "acceptEdits"
    assert perms.check(tools["edit_file"], {"path": "a"})[0] == "allow"

def test_readonly_registry_excludes_write_tools():
    names = set(build_tools(read_only=True))
    assert "bash" not in names and "write_file" not in names and "task" not in names
    assert "read_file" in names and "grep" in names
