"""Friday Code terminal: request lifecycle, retry, command menu, copy and scene commands.

Runs the real Textual app against the real agent API router in-process, with a scripted model.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from friday_agent import engine, terminal


def _script(replies):
    replies = list(replies)

    async def fake_stream(model, messages, tools=None, options=None):
        reply = replies.pop(0) if replies else "ok"
        if isinstance(reply, Exception):
            raise reply
        for piece in (reply[i:i + 5] for i in range(0, len(reply), 5)):
            yield {"message": {"content": piece}, "done": False}
        yield {"message": {"content": ""}, "done": True, "prompt_eval_count": 12, "eval_count": 4,
               "prompt_eval_duration": 10_000_000, "eval_duration": 40_000_000}
    return fake_stream


@pytest.fixture
def api(tmp_path: Path, monkeypatch):
    from backend.app import agent_routes

    monkeypatch.setattr(agent_routes, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(agent_routes, "PERSIST_PATH", tmp_path / "data" / "agent_sessions.json")
    monkeypatch.setattr(agent_routes, "SESSIONS", {})

    async def no_warm(session):
        return None

    monkeypatch.setattr(agent_routes, "warm_session", no_warm)
    monkeypatch.setattr(terminal, "HISTORY_PATH", tmp_path / "history")
    app = FastAPI()
    app.include_router(agent_routes.router)
    real_client = httpx.AsyncClient

    def client(*args, **kwargs):
        kwargs["transport"] = httpx.ASGITransport(app=app)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(terminal.httpx, "AsyncClient", client)
    return agent_routes


async def _until(pred, timeout=10.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return True
        await asyncio.sleep(0.05)
    return False


def _run(coro):
    return asyncio.run(coro)


def test_answer_lifecycle_branding_and_copy(api, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _script(["Here:\n```python\nprint('hi')\n```"]))

    async def scenario():
        app = terminal.FridayAgentApp(api="http://friday.test")
        async with app.run_test(size=(140, 44)) as pilot:
            assert await _until(lambda: app.session_id)
            assert app.TITLE == "Friday Code"
            sessions = list(api.SESSIONS.values())
            assert sessions[0].tool_routing == "auto"  # the terminal asks for routed tools
            app.query_one("#prompt").value = "show me hello world in python"
            await pilot.press("enter")
            assert await _until(lambda: app.request_state == "completed" and not app.busy)
            assert "print('hi')" in app.last_reply
            assert app.last_metrics.get("status") == "completed"
            await pilot.press("ctrl+y")
            await pilot.pause(0.1)
            assert app.clipboard == "print('hi')"
    _run(scenario())


def test_failure_keeps_question_and_ctrl_r_retries(api, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _script(["", "", "", "Recovered answer"]))

    async def scenario():
        app = terminal.FridayAgentApp(api="http://friday.test")
        async with app.run_test(size=(140, 44)) as pilot:
            assert await _until(lambda: app.session_id)
            app.query_one("#prompt").value = "hello there"
            await pilot.press("enter")
            assert await _until(lambda: app.request_state == "failed" and not app.busy)
            assert app.last_failed_prompt == "hello there"
            assert app.query_one("#prompt").value == "hello there"  # the question is given back
            assert app.last_reply == ""                           # no invented answer
            await pilot.press("ctrl+r")
            assert await _until(lambda: app.request_state == "completed" and not app.busy)
            assert app.last_reply == "Recovered answer"
            assert app.last_failed_prompt == ""
    _run(scenario())


def test_backend_unreachable_is_reported_not_hung(monkeypatch, tmp_path):
    monkeypatch.setattr(terminal, "HISTORY_PATH", tmp_path / "history")

    async def scenario():
        app = terminal.FridayAgentApp(api="http://127.0.0.1:9")
        async with app.run_test(size=(120, 40)):
            assert await _until(lambda: app.link == "offline", 15)
            assert not app.busy
    _run(scenario())


def test_command_menu_filters_and_runs(api, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _script([]))

    async def scenario():
        app = terminal.FridayAgentApp(api="http://friday.test")
        async with app.run_test(size=(140, 44)) as pilot:
            assert await _until(lambda: app.session_id)
            await pilot.press("/", "s", "c")
            await pilot.pause(0.1)
            menu = app.command_menu
            assert menu.display and menu.get_option_at_index(0).id.startswith("/scene")
            await pilot.press("escape")
            assert not menu.display
            app.query_one("#prompt").value = ""
            await pilot.press("/", "s", "t")
            await pilot.pause(0.1)
            assert menu.display
            await pilot.press("enter")  # /stats needs no arguments: runs directly
            assert await _until(lambda: not app.busy)
            assert app.show_stats is True
    _run(scenario())


def test_scene_commands_and_single_timer(api, monkeypatch):
    monkeypatch.setattr(engine, "chat_stream", _script([]))

    async def scenario():
        app = terminal.FridayAgentApp(api="http://friday.test")
        async with app.run_test(size=(140, 44)) as pilot:
            assert await _until(lambda: app.session_id)
            view = app.query_one("#scene")
            timer = view._timer
            view.on_mount()  # a second mount must not start a second animation timer
            assert view._timer is timer
            for command, expect in (("/scene winter", "winter"), ("/scene rainy", "rainy"),
                                    ("/scene night", "night")):
                app.query_one("#prompt").value = command
                await pilot.press("enter")
                assert await _until(lambda: not app.busy)
                assert expect in app.scene.describe()
            await pilot.press("f3")
            assert not app.query_one("#scene").display
            await pilot.pause(0.5)
            assert view.frames > 0
    _run(scenario())
