import asyncio
import sys
from pathlib import Path

import pytest

from friday_agent.engine import create_session, rewind
from friday_agent.hooks import Hooks
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

def test_network_commands_require_approval_by_default(tmp_path: Path):
    bash = build_tools()["bash"]
    perms = Permissions(tmp_path.resolve(), settings={"network": "ask"})
    assert perms.check(bash, {"command": "curl https://example.com"})[0] == "ask"
    denied = Permissions(tmp_path.resolve(), settings={"network": "deny"})
    assert denied.check(bash, {"command": "curl https://example.com"})[0] == "deny"

def test_pre_tool_hook_blocks_and_rewind_restores_turn(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("friday_agent.config.HOME", tmp_path / "home")
    monkeypatch.setattr("friday_agent.permissions.HOME", tmp_path / "home")
    hook = Hooks(
        {"PreToolUse": [{
            "matcher": "write_file",
            "command": f'"{sys.executable}" -c "import sys; sys.exit(7)"',
        }]},
        tmp_path,
    )
    allowed, message = hook.run("PreToolUse", "write_file", {"path": "new.txt"})
    assert not allowed
    assert message

    session = create_session(tmp_path)
    session.context.begin_turn(len(session.messages), "create new file")
    session.messages.append({"role": "user", "content": "create new file"})
    run(build_tools()["write_file"].run(
        {"path": "new.txt", "content": "created"}, session.context
    ))
    session.messages.append({"role": "assistant", "content": "done"})

    assert rewind(session) == ["create new file"]
    assert not (tmp_path / "new.txt").exists()
    assert len(session.messages) == 1
