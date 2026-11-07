import json
import tarfile
import zipfile
from unittest.mock import Mock

import httpx
import pytest
import respx

from localforge.core import ForgeError, Store, atomic_json, redact
from localforge.install import extract, node_compatible
from localforge.runtime import Runtime
from localforge.system import detect


@pytest.mark.parametrize(
    "version,expected",
    [
        ("v22.19.0", False),
        ("24.15.0", False),
        ("24.16.0", True),
        ("25.0.0", False),
        ("26.1.0", True),
        ("nonsense", False),
    ],
)
def test_node_version(version, expected):
    assert node_compatible(version) == expected


def test_archive_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("../outside", "evil")
    with pytest.raises(ForgeError):
        extract(archive, tmp_path / "stage")
    assert not (tmp_path / "outside").exists()


def test_archive_symlink(tmp_path):
    archive = tmp_path / "evil.tar"
    with tarfile.open(archive, "w") as t:
        info = tarfile.TarInfo("escape")
        info.type = tarfile.SYMTYPE
        info.linkname = "/tmp"
        t.addfile(info)
    with pytest.raises(ForgeError):
        extract(archive, tmp_path / "stage")


def test_state_survives_restart(tmp_path):
    store = Store(tmp_path)
    store.update(install_stage="Node.js", install_error="Interrupted")
    assert Store(tmp_path).state["install_stage"] == "Node.js"
    import os

    if os.name != "nt":
        assert (tmp_path / "state.json").stat().st_mode & 0o077 == 0


def test_system_detection(tmp_path):
    info = detect(tmp_path, network=False)
    assert info.os and info.arch and info.ram_total > 0 and info.disk_free > 0
    assert info.ram_available <= info.ram_total
    assert all(isinstance(x, bool) for x in info.ports.values())


def test_safe_config(tmp_path):
    rt = Runtime(Store(tmp_path), vault=Mock())
    cfg = rt.config({"engine": "ollama", "model": "test", "context": 16384})
    assert cfg["gateway"]["bind"] == "loopback"
    assert cfg["tools"]["fs"]["workspaceOnly"] is True
    assert "exec" in cfg["tools"]["deny"]
    assert cfg["models"]["providers"]["ollama"]["api"] == "ollama"
    assert not cfg["models"]["providers"]["ollama"]["baseUrl"].endswith("/v1")


def test_config_rollback(tmp_path, monkeypatch):
    rt = Runtime(Store(tmp_path), vault=Mock())
    path = tmp_path / "openclaw/openclaw.json"
    atomic_json(path, {"previous": True})
    monkeypatch.setattr(rt.installer, "claw", lambda: ["openclaw"])
    monkeypatch.setattr("localforge.runtime.run", Mock(side_effect=ForgeError("invalid schema")))
    with pytest.raises(ForgeError):
        rt.write_config({"bad": True})
    assert json.loads(path.read_text()) == {"previous": True}


def test_uninstall_preserves_user_files(tmp_path):
    store = Store(tmp_path)
    workspace = tmp_path / "workspace/user.txt"
    workspace.write_text("precious")
    model = tmp_path / "models/a.gguf"
    model.write_text("weights")
    binary = tmp_path / "runtime/node"
    binary.write_text("runtime")
    Runtime(store, vault=Mock()).uninstall()
    assert workspace.read_text() == "precious" and model.exists() and not binary.exists()


def test_uninstall_refuses_symlinks(tmp_path):
    store = Store(tmp_path / "app")
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "runtime").rmdir()
    try:
        (store.root / "runtime").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("This host does not grant symlink creation privileges.")
    with pytest.raises(ForgeError):
        Runtime(store, vault=Mock()).uninstall()
    assert outside.exists()


def test_no_shell_execution(monkeypatch):
    from localforge.core import run

    result = Mock(returncode=0)
    result.communicate.return_value = ("ok", "")
    mock = Mock(return_value=result)
    monkeypatch.setattr("subprocess.Popen", mock)
    run(["program", "$(touch bad); echo hi"])
    assert mock.call_args.args[0][1] == "$(touch bad); echo hi"
    assert not mock.call_args.kwargs.get("shell", False)


@pytest.mark.parametrize("users", ["", "*", "123,*", "abc", "123; rm -rf /"])
def test_messaging_rejects_unauthorized_allowlist(tmp_path, users):
    rt = Runtime(Store(tmp_path), vault=Mock())
    with pytest.raises(ForgeError, match="allowlist"):
        rt.configure_messaging("telegram", "secret", users)


@respx.mock
def test_messaging_authentication_and_policy(tmp_path, monkeypatch):
    vault = Mock()
    vault.get.return_value = None
    rt = Runtime(Store(tmp_path), vault=vault)
    respx.get("https://api.telegram.org/botsecret/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    write = Mock()
    monkeypatch.setattr(rt, "write_config", write)
    rt.configure_messaging("telegram", "secret", "123,456")
    cfg = write.call_args.args[0]["channels"]["telegram"]
    assert cfg["allowFrom"] == ["123", "456"] and cfg["dmPolicy"] == "allowlist"
    assert cfg["groupPolicy"] == "disabled" and cfg["botToken"] == "${TELEGRAM_BOT_TOKEN}"
    vault.set.assert_called_once_with("telegram", "secret")
    assert "secret" not in (tmp_path / "state.json").read_text()


def test_secrets_redacted():
    assert "hf_abcd123" not in redact("failed hf_abcd123")
    assert "12345678:ABCdefghijklmnopqrstuv" not in redact("12345678:ABCdefghijklmnopqrstuv")


@respx.mock
def test_inference_is_real_http_and_empty_fails(tmp_path):
    rt = Runtime(Store(tmp_path), vault=Mock())
    route = respx.post("http://127.0.0.1:11435/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "Hello"}})
    )
    c = {"engine": "ollama", "model": "local", "context": 16384}
    assert rt.infer(c) == "Hello"
    assert route.called
    route.mock(return_value=httpx.Response(200, json={"message": {"content": ""}}))
    with pytest.raises(ForgeError, match="text response"):
        rt.infer(c)


def test_tool_failure_falls_back_to_explicitly_disabled_tools(tmp_path, monkeypatch):
    rt = Runtime(Store(tmp_path), vault=Mock())
    monkeypatch.setattr(rt, "stop", Mock())
    monkeypatch.setattr(rt, "start_gateway", Mock())
    write = Mock()
    monkeypatch.setattr(rt, "write_config", write)
    monkeypatch.setattr(rt, "agent_turn", Mock(side_effect=[ForgeError("does not support tools"), "Hello"]))
    candidate = {"engine": "ollama", "model": "tiny", "context": 16384}
    report = rt.verify_agent(candidate)
    assert report["agent"] is True and report["tools"] is False
    assert candidate["tools_enabled"] is False
    assert write.call_args.args[0]["tools"]["deny"] == ["*"]
    assert not (tmp_path / "workspace/verification.txt").exists()


def test_no_false_tool_success_from_text(tmp_path, monkeypatch):
    rt = Runtime(Store(tmp_path), vault=Mock())
    monkeypatch.setattr(rt, "stop", Mock())
    monkeypatch.setattr(rt, "start_gateway", Mock())
    monkeypatch.setattr(rt, "write_config", Mock())
    monkeypatch.setattr(rt, "agent_turn", Mock(return_value="I have successfully read the file."))
    report = rt.verify_agent({"engine": "ollama", "model": "tiny", "context": 16384})
    assert report["tools"] is False


@respx.mock
def test_messaging_config_failure_restores_credential(tmp_path, monkeypatch):
    vault = Mock()
    vault.get.return_value = "old-valid-secret"
    rt = Runtime(Store(tmp_path), vault=vault)
    respx.get("https://api.telegram.org/botnew-secret/getMe").respond(200, json={"ok": True})
    monkeypatch.setattr(rt, "write_config", Mock(side_effect=ForgeError("schema")))
    with pytest.raises(ForgeError):
        rt.configure_messaging("telegram", "new-secret", "123")
    vault.set.assert_called_with("telegram", "old-valid-secret")
    assert not rt.store.state["telegram_configured"]


def test_registered_secrets_are_redacted():
    from localforge.core import protect_secret

    protect_secret("discord-secret-no-prefix")
    assert "discord-secret-no-prefix" not in redact("error discord-secret-no-prefix")


def test_port_check_rejects_active_listener():
    import socket
    from localforge.system import port_free

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        assert not port_free(listener.getsockname()[1])


@pytest.mark.parametrize(
    "event_type,role,tool,error,expected",
    [
        ("tool.result", "toolResult", "read", False, True),
        ("assistant.message", "assistant", "read", False, False),
        ("tool.result", "toolResult", "exec", False, False),
        ("tool.result", "toolResult", "read", True, False),
    ],
)
def test_exported_tool_evidence(tmp_path, monkeypatch, event_type, role, tool, error, expected):
    rt = Runtime(Store(tmp_path), vault=Mock())
    monkeypatch.setattr(rt.installer, "claw", lambda: ["openclaw"])

    def export(args, **kwargs):
        name = args[args.index("--output") + 1]
        folder = tmp_path / "workspace/.openclaw/trajectory-exports" / name
        folder.mkdir(parents=True)
        event = {
            "sessionId": "test-session",
            "type": event_type,
            "data": {
                "message": {
                    "role": role,
                    "toolName": tool,
                    "isError": error,
                    "content": [{"text": "secret-nonce"}],
                }
            },
        }
        (folder / "events.jsonl").write_text(json.dumps(event) + "\n")
        return "{}"

    monkeypatch.setattr("localforge.runtime.run", export)
    assert rt.read_tool_verified("test-session", "secret-nonce") is expected
    assert not list((tmp_path / "workspace/.openclaw/trajectory-exports").iterdir())


def test_fallback_declares_no_tool_capability(tmp_path):
    rt = Runtime(Store(tmp_path), vault=Mock())
    cfg = rt.config({"engine": "ollama", "model": "tiny", "context": 16384, "tools_enabled": False})
    assert cfg["tools"]["deny"] == ["*"] and "allow" not in cfg["tools"]
    assert cfg["models"]["providers"]["ollama"]["models"][0]["compat"]["supportsTools"] is False


def test_reinstall_staging_preserves_model_but_invalidates_readiness(tmp_path, monkeypatch):
    store = Store(tmp_path)
    candidate = {"engine": "ollama", "model": "existing", "context": 16384}
    store.update(active_model=candidate, verification={"tools": True})
    rt = Runtime(store, vault=Mock())
    write = Mock()
    monkeypatch.setattr(rt, "write_config", write)
    rt.stage()
    assert store.state["active_model"] == candidate
    assert store.state["verification"] == {}
    assert write.call_args.args[0]["agents"]["defaults"]["model"]["primary"] == "ollama/existing"
