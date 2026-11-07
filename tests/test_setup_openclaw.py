from unittest.mock import Mock
import json

import httpx
import pytest
import respx

from localforge.core import ForgeError, Store
from localforge.setup_openclaw import choose_model, prepare


def test_reuses_previously_downloaded_model_without_discovery(tmp_path, monkeypatch):
    store = Store(tmp_path)
    path = tmp_path / "model.gguf"
    path.write_bytes(b"model")
    model = {"path": str(path), "sha256": "a" * 64, "engine": "ollama"}
    store.update(downloaded=model)
    discover = Mock(side_effect=AssertionError("must not download again"))
    monkeypatch.setattr("localforge.setup_openclaw.discover", discover)
    assert choose_model(store) == model


def fake_runtime(tmp_path):
    rt = Mock()
    rt.store = Store(tmp_path)
    rt.gateway_port = 18790
    rt.register.return_value = {"context": 16384, "model": "local", "engine": "ollama"}
    rt.config.side_effect = lambda _: {"agents": {"defaults": {}}}
    rt.agent_turn.return_value = "Hello from OpenClaw"
    rt.installer.claw.return_value = ["openclaw"]
    rt.env.return_value = {}
    return rt


@respx.mock
def test_prepares_real_dashboard_handoff_and_preserves_workspace(tmp_path, monkeypatch):
    rt = fake_runtime(tmp_path)
    instructions = rt.store.root / "workspace/AGENTS.md"
    instructions.write_text("Keep my instructions")
    url = "http://127.0.0.1:18790/#bootstrap=test"
    command = Mock(return_value=json.dumps({"browserUrl": url}))
    monkeypatch.setattr("localforge.setup_openclaw.run", command)
    respx.get("http://127.0.0.1:18790/").mock(return_value=httpx.Response(200, text="<html>OpenClaw</html>"))
    result = prepare(rt, {"path": "/model", "sha256": "a" * 64, "engine": "ollama"})
    assert result == (url, "Hello from OpenClaw")
    assert instructions.read_text() == "Keep my instructions"
    assert command.call_args.args[0] == ["openclaw", "dashboard", "--json"]
    assert rt.store.state["verification"]["agent"]
    assert not rt.store.state["verification"]["tools"]


@respx.mock
def test_tool_incompatibility_retries_openclaw_chat_without_tools(tmp_path, monkeypatch):
    rt = fake_runtime(tmp_path)
    rt.agent_turn.side_effect = [ForgeError("tools unsupported"), "Hello"]
    monkeypatch.setattr(
        "localforge.setup_openclaw.run",
        lambda *a, **k: json.dumps({"browserUrl": "http://127.0.0.1:18790/#bootstrap=test"}),
    )
    respx.get("http://127.0.0.1:18790/").mock(return_value=httpx.Response(200, text="<html>OpenClaw</html>"))
    assert prepare(rt, {"path": "/model", "sha256": "a" * 64, "engine": "ollama"})[1] == "Hello"
    assert rt.store.state["candidate"]["tools_enabled"] is False
    assert rt.agent_turn.call_count == 2


def test_does_not_open_untrusted_dashboard_url(tmp_path, monkeypatch):
    rt = fake_runtime(tmp_path)
    monkeypatch.setattr(
        "localforge.setup_openclaw.run", lambda *a, **k: json.dumps({"browserUrl": "https://evil.test/"})
    )
    with pytest.raises(ForgeError, match="authenticated local dashboard"):
        prepare(rt, {"path": "/model", "sha256": "a" * 64, "engine": "ollama"})
