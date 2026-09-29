import asyncio
import sys
from pathlib import Path

import pytest

from friday_agent import config, engine
from friday_agent.engine import context_report, create_session, rewind
from friday_agent.git_ops import batch_worktrees
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

    plan = Permissions(tmp_path.resolve(), "plan", settings={"network": "ask"})
    assert plan.check(build_tools()["bash"], {"command": "curl https://example.com"})[0] == "deny"
    assert plan.check(build_tools()["web_fetch"], {"url": "https://example.com"})[0] == "ask"

def test_git_mutations_are_permission_gated(tmp_path: Path):
    tools = build_tools()
    perms = Permissions(tmp_path.resolve(), settings={"network": "ask"})
    assert perms.check(tools["git_commit"], {"message": "test"})[0] == "ask"
    assert perms.check(tools["git_branch"], {"name": "feature/test"})[0] == "ask"
    assert perms.check(tools["create_pr"], {"title": "test", "body": "test"})[0] == "ask"

def test_project_and_home_settings_merge(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "workspace"
    (home / "commands").mkdir(parents=True)
    (root / ".friday").mkdir(parents=True)
    (home / "settings.json").write_text('{"network":"allow","permissions":{"allow":["bash:pytest"]}}')
    (root / ".friday" / "settings.json").write_text('{"network":"ask","theme":"ember"}')
    (home / "commands" / "check.md").write_text("Run tests: $ARGUMENTS")
    (root / "FRIDAY.md").write_text("Project rule")
    monkeypatch.setattr(config, "HOME", home)

    settings = config.load_settings(root)
    assert settings["network"] == "ask"
    assert settings["theme"] == "ember"
    assert settings["permissions"]["allow"] == ["bash:pytest"]
    assert config.load_project_memory(root) == "Project rule"
    assert config.load_custom_commands(root)["check"] == "Run tests: $ARGUMENTS"

def test_git_tool_registration_and_batch_input_validation(tmp_path: Path):
    tools = build_tools()
    assert {"git_commit", "git_branch", "create_pr", "git_review_diff", "batch_worktrees"} <= set(tools)
    with pytest.raises(ToolError):
        run(batch_worktrees({"tasks": ["Bad Name"]}, Context(root=tmp_path.resolve())))

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

def test_context_compaction_tracks_usage(tmp_path: Path, monkeypatch):
    async def fake_chat_stream(model, messages, tools=None, options=None):
        yield {
            "message": {"content": "Keep the implementation and tests in sync."},
            "done": True,
            "prompt_eval_count": 11,
            "eval_count": 7,
        }

    monkeypatch.setattr("friday_agent.config.HOME", tmp_path / "home")
    monkeypatch.setattr("friday_agent.permissions.HOME", tmp_path / "home")
    monkeypatch.setattr(engine, "chat_stream", fake_chat_stream)
    session = create_session(tmp_path)
    session.messages.append({"role": "user", "content": "older context"})

    assert run(engine.compact_session(session)) == "Keep the implementation and tests in sync."
    report = context_report(session)
    assert report["messages"] == 2
    assert report["input_tokens"] == 11
    assert report["output_tokens"] == 7

def test_parallel_subagent_tool_is_bounded_and_read_only():
    context = Context(root=Path.cwd())

    async def runner(tasks):
        return ",".join(tasks)

    context.subagent_parallel_runner = runner
    result = run(build_tools()["spawn_subagent"].run({"tasks": ["inspect A", "inspect B"]}, context))
    assert result == "inspect A,inspect B"
    with pytest.raises(ToolError):
        run(build_tools()["spawn_subagent"].run({"tasks": ["a"] * 5}, context))
