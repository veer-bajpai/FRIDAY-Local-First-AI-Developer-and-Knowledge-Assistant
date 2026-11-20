import asyncio
import json
import sys
import subprocess
import types
from contextlib import asynccontextmanager
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

def test_fullscreen_terminal_app_mounts_without_live_backend():
    pytest.importorskip("textual")
    from friday_agent.terminal import FridayAgentApp

    app = FridayAgentApp(api="http://127.0.0.1:1")

    async def fake_json(method, path, body=None):
        if path == "/api/agent/sessions":
            return {"id": "test-session", "mode": "default", "model": "test-model", "workspace": "test"}
        if path == "/api/agent/commands":
            return {"check": "Run the project checks"}
        return {}

    async def run_app():
        app._json = fake_json
        async with app.run_test() as pilot:
            assert app.query_one("#transcript")
            assert app.query_one("#prompt").disabled is False
            guide = app.query_one("#guide-content")
            assert "/plan" in str(guide.content)
            assert "/check" in str(guide.content)

            app._start_thinking()
            app.thinking_started_at -= 3.1
            app._tick_thinking()
            assert "Reading context" in str(app.query_one("#thinking").content)
            app._set_streaming()
            assert "Streaming response" in str(app.query_one("#thinking").content)
            app._finish_thinking()
            assert "Thought for 3s" in str(app.query_one("#thinking").content)
            assert app.thinking_timer is None
            await pilot.press("tab")

    run(run_app())

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

def test_turn_uses_configured_output_token_budget(tmp_path: Path, monkeypatch):
    options_seen = []

    async def fake_chat_stream(model, messages, tools=None, options=None):
        options_seen.append(options)
        yield {"message": {"content": "Complete answer."}, "done": True}

    monkeypatch.setattr(engine, "NUM_PREDICT", 2048, raising=False)
    monkeypatch.setattr(engine, "chat_stream", fake_chat_stream)
    session = create_session(tmp_path)

    events = run(_collect_events(engine.run_turn(session, "Explain this.")))

    assert options_seen == [{"num_ctx": config.NUM_CTX, "num_predict": 2048}]
    assert "".join(event["content"] for event in events if event["type"] == "token") == "Complete answer."

async def _collect_events(events):
    return [event async for event in events]

def test_parallel_subagent_tool_is_bounded_and_read_only():
    context = Context(root=Path.cwd())

    async def runner(tasks):
        return ",".join(tasks)

    context.subagent_parallel_runner = runner
    result = run(build_tools()["spawn_subagent"].run({"tasks": ["inspect A", "inspect B"]}, context))
    assert result == "inspect A,inspect B"
    with pytest.raises(ToolError):
        run(build_tools()["spawn_subagent"].run({"tasks": ["a"] * 5}, context))

def test_git_tools_work_in_an_isolated_repository(tmp_path: Path, monkeypatch):
    from friday_agent import git_ops

    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Friday Tests"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "friday-tests@example.invalid"], cwd=root, check=True)
    sample = root / "sample.txt"
    sample.write_text("before\n")
    subprocess.run(["git", "add", "sample.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=root, check=True, capture_output=True)
    sample.write_text("after\n")

    context = Context(root=root.resolve())
    assert "before" in run(git_ops.git_review_diff({}, context))
    assert run(git_ops.git_branch({"name": "feature/test"}, context))
    assert subprocess.run(
        ["git", "branch", "--show-current"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip() == "feature/test"
    assert "On branch" not in run(git_ops.git_branch({}, context))  # list mode works
    run(git_ops.git_add({"paths": ["sample.txt"]}, context))  # commits only what was staged
    assert "update sample" in run(git_ops.git_commit({"message": "update sample"}, context))
    assert not subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert "Worktrees ready" in run(batch_worktrees({"tasks": ["parallel-check"]}, context))
    assert (tmp_path / "repo-wt-parallel-check").is_dir()

    commands = []

    async def fake_run(ctx, *args, timeout=300):
        commands.append(args)
        return "ok"

    monkeypatch.setattr(git_ops, "_run_cmd", fake_run)
    assert run(git_ops.git_review_diff({"pr": 42}, context)) == "ok"
    with pytest.raises(ToolError, match="positive integer"):
        run(git_ops.git_review_diff({"pr": "invalid"}, context))
    assert "ok" in run(git_ops.create_pr({"title": "Title", "body": "Body"}, context))
    assert commands == [
        ("gh", "pr", "diff", "42"),
        ("git", "push", "-u", "origin", "HEAD"),
        ("gh", "pr", "create", "--title", "Title", "--body", "Body"),
    ]

def test_mcp_manager_registers_tools_without_starting_a_real_server(tmp_path: Path, monkeypatch):
    from friday_agent.mcp_client import MCPManager

    config_path = tmp_path / "mcp.json"
    config_path.write_text(json.dumps({"servers": {"test": {"command": "fake-server"}}}))

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def initialize(self):
            return None

        async def list_tools(self):
            definition = types.SimpleNamespace(
                name="echo", description="Echo test input", inputSchema={"type": "object"}
            )
            return types.SimpleNamespace(tools=[definition])

        async def call_tool(self, name, args):
            return types.SimpleNamespace(
                isError=False, content=[types.SimpleNamespace(text=f"{name}: {args['value']}")]
            )

    @asynccontextmanager
    async def fake_stdio_client(params):
        assert params.command == "fake-server"
        yield object(), object()

    mcp_module = types.ModuleType("mcp")
    mcp_module.ClientSession = lambda *_args: FakeSession()
    mcp_module.StdioServerParameters = lambda **kwargs: types.SimpleNamespace(**kwargs)
    client_module = types.ModuleType("mcp.client")
    stdio_module = types.ModuleType("mcp.client.stdio")
    stdio_module.stdio_client = fake_stdio_client
    monkeypatch.setitem(sys.modules, "mcp", mcp_module)
    monkeypatch.setitem(sys.modules, "mcp.client", client_module)
    monkeypatch.setitem(sys.modules, "mcp.client.stdio", stdio_module)

    async def exercise():
        manager = MCPManager(config_path)
        await manager.start()
        tool = manager.tools["mcp__test__echo"]
        result = await tool.run({"value": "works"}, Context(root=tmp_path.resolve()))
        await manager.close()
        return result

    assert run(exercise()) == "echo: works"

def test_voice_input_transcribes_with_local_dependencies_mocked(monkeypatch):
    from friday_agent import voice

    class FakeAudio:
        def reshape(self, *_shape):
            return "audio-samples"

    sounddevice = types.ModuleType("sounddevice")
    sounddevice.rec = lambda *args, **kwargs: FakeAudio()
    sounddevice.wait = lambda: None
    numpy = types.ModuleType("numpy")
    numpy.asarray = lambda audio: audio
    faster_whisper = types.ModuleType("faster_whisper")
    faster_whisper.WhisperModel = object
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setitem(sys.modules, "numpy", numpy)
    monkeypatch.setitem(sys.modules, "faster_whisper", faster_whisper)

    class FakeModel:
        def transcribe(self, audio):
            assert audio == "audio-samples"
            return [types.SimpleNamespace(text=" hello "), types.SimpleNamespace(text="Friday")], None

    monkeypatch.setattr(voice, "_model", lambda: FakeModel())
    assert voice.listen(seconds=2) == "hello Friday"
    with pytest.raises(ValueError, match="between 1 and 30 seconds"):
        voice.listen(seconds=0)

def test_insights_report_ignores_bad_rows_and_escapes_tool_names(tmp_path: Path, monkeypatch):
    from friday_agent import insights

    root = tmp_path / "workspace"
    usage = root / ".friday" / "usage.jsonl"
    usage.parent.mkdir(parents=True)
    usage.write_text(
        json.dumps({"tool": "<script>alert(1)</script>", "ok": False}) + "\nnot-json\n",
        encoding="utf-8",
    )
    report = insights.build_report(root)
    content = report.read_text(encoding="utf-8")
    assert report.is_file()
    assert "<strong>1</strong><br>tool calls" in content  # the bad row was skipped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content
    assert "<script>alert(1)</script>" not in content

def test_agent_http_session_and_integration_routes_are_mockable(tmp_path: Path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app import agent_routes

    root = tmp_path / "workspace"
    (root / ".friday" / "commands").mkdir(parents=True)
    (root / ".friday" / "commands" / "check.md").write_text("Run checks")
    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", root)
    monkeypatch.setattr(agent_routes, "SESSIONS", {})

    async def fake_models():
        return ["test-model"]

    turn_prompts = []

    async def fake_run_turn(session, prompt, reserved=False):
        turn_prompts.append(prompt)
        yield {"type": "token", "content": f"handled: {prompt}"}
        yield {"type": "done", "mode": session.mode, "todos": [], "undo_count": 0}

    async def fake_compact(session):
        return "Session summary"

    monkeypatch.setattr(agent_routes, "list_models", fake_models)
    monkeypatch.setattr(agent_routes, "run_turn", fake_run_turn)
    monkeypatch.setattr(agent_routes, "compact_session", fake_compact)
    monkeypatch.setattr(agent_routes, "listen", lambda: "voice text")
    report = root / "insights.html"
    monkeypatch.setattr(agent_routes, "build_report", lambda _root: report)
    opened = []
    monkeypatch.setattr(agent_routes.webbrowser, "open", lambda uri: opened.append(uri))

    app = FastAPI()
    app.include_router(agent_routes.router)
    app.dependency_overrides[agent_routes.require_local_agent_request] = lambda: None
    with TestClient(app) as client:
        headers = {"origin": "http://localhost:3000"}
        assert client.get("/api/agent/models", headers=headers).json()["models"] == ["test-model"]
        assert client.get("/api/agent/commands", headers=headers).json() == {"check": "Run checks"}
        assert client.post("/api/agent/voice", headers=headers).json() == {"text": "voice text"}
        assert client.get("/api/agent/insights", headers=headers).json()["path"] == str(report)
        assert opened == [report.as_uri()]

        first = client.post("/api/agent/sessions", headers=headers, json={"mode": "default"})
        second = client.post("/api/agent/sessions", headers=headers, json={"mode": "default"})
        assert first.status_code == second.status_code == 200
        first_id, second_id = first.json()["id"], second.json()["id"]
        assert len(client.get("/api/agent/sessions", headers=headers).json()) == 2
        assert client.get(f"/api/agent/sessions/{first_id}", headers=headers).json()["id"] == first_id
        assert client.patch(
            f"/api/agent/sessions/{first_id}", headers=headers, json={"mode": "plan"}
        ).json()["mode"] == "plan"
        assert client.get(f"/api/agent/sessions/{first_id}/context", headers=headers).status_code == 200

        delivered = run(
            agent_routes.SESSIONS[first_id].context.session_message_runner(second_id[:8], "coordinate")
        )
        assert "Message delivered" in delivered
        response = client.post(
            f"/api/agent/sessions/{second_id}/turns", headers=headers, json={"prompt": "hello"}
        )
        assert response.status_code == 200
        assert "handled: [message from agent" in response.text
        assert "hello" in response.text
        assert "text/event-stream" in response.headers["content-type"]
        assert "coordinate" in turn_prompts[-1]
        assert client.post(f"/api/agent/sessions/{first_id}/compact", headers=headers).json()["summary"] == "Session summary"

        first_session = agent_routes.SESSIONS[first_id]
        async def approve_pending_call():
            future = asyncio.get_running_loop().create_future()
            first_session.pending_approvals["pending"] = future
            response = await agent_routes.approve_tool_call(
                first_id, "pending", agent_routes.ApprovalRequest(decision="always")
            )
            assert future.result() == "always"
            return response

        assert run(approve_pending_call())["decision"] == "always"

        rewind_file = root / "rewound.txt"
        rewind_file.write_text("created")
        first_session.messages.extend([
            {"role": "user", "content": "create file"},
            {"role": "assistant", "content": "done"},
        ])
        first_session.context.checkpoints.append((rewind_file, None))
        first_session.context.turns.append({
            "msg_index": 1, "label": "create file", "files": [(rewind_file, None)]
        })
        rewind_response = client.post(
            f"/api/agent/sessions/{first_id}/rewind", headers=headers, json={"count": 1}
        )
        assert rewind_response.json()["rewound"] == ["create file"]
        assert not rewind_file.exists()

        changed = root / "reversible.txt"
        changed.write_text("created")
        first_session.context.checkpoints.append((changed, None))
        assert client.post(f"/api/agent/sessions/{first_id}/undo", headers=headers).status_code == 200
        assert not changed.exists()
        assert client.delete(f"/api/agent/sessions/{first_id}", headers=headers).json() == {"deleted": True}
        assert client.delete(f"/api/agent/sessions/{second_id}", headers=headers).json() == {"deleted": True}

def test_local_agent_api_rejects_remote_client_with_spoofed_local_origin():
    from fastapi import HTTPException
    from starlette.requests import Request

    from backend.app.agent_routes import require_local_agent_request

    def request_for(client, origin=None):
        headers = [(b"origin", origin.encode())] if origin else []
        return Request({
            "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
            "method": "GET", "scheme": "http", "path": "/", "raw_path": b"/",
            "query_string": b"", "headers": headers, "client": client,
            "server": ("localhost", 8000),
        })

    require_local_agent_request(request_for(("127.0.0.1", 1234), "http://localhost:3000"))
    with pytest.raises(HTTPException) as exc:
        require_local_agent_request(request_for(("203.0.113.8", 1234), "http://localhost:3000"))
    assert exc.value.status_code == 403

