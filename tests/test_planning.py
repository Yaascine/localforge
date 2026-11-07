import pytest
import respx

from localforge.core import ForgeError, Store
from localforge.install import Installer


@pytest.mark.parametrize(
    "system,arch,node_file,engine_file",
    [
        ("Darwin", "arm64", "node-v26.13.0-darwin-arm64.tar.gz", "ollama-darwin.tgz"),
        ("Darwin", "x86_64", "node-v26.13.0-darwin-x64.tar.gz", "ollama-darwin.tgz"),
        ("Windows", "AMD64", "node-v26.13.0-win-x64.zip", "ollama-windows-amd64.zip"),
        ("Linux", "x86_64", "node-v26.13.0-linux-x64.tar.gz", "ollama-linux-amd64.tar.zst"),
        ("Linux", "aarch64", "node-v26.13.0-linux-arm64.tar.gz", "ollama-linux-arm64.tar.zst"),
    ],
)
@respx.mock
def test_native_planning(system, arch, node_file, engine_file, tmp_path, monkeypatch):
    monkeypatch.setattr("localforge.install.platform.system", lambda: system)
    monkeypatch.setattr("localforge.install.platform.machine", lambda: arch)
    monkeypatch.setattr("localforge.install.shutil.which", lambda _: None)
    respx.get("https://nodejs.org/dist/index.json").respond(200, json=[{"version": "v26.13.0", "lts": "LTS"}])
    respx.get("https://nodejs.org/dist/v26.13.0/SHASUMS256.txt").respond(
        200, text="a" * 64 + "  " + node_file
    )
    respx.get("https://registry.npmjs.org/openclaw/latest").respond(200, json={"version": "2026.11.1"})
    respx.get("https://api.github.com/repos/ollama/ollama/releases/latest").respond(
        200,
        json={
            "tag_name": "v1.0.0",
            "assets": [
                {
                    "name": engine_file,
                    "digest": "sha256:" + "b" * 64,
                    "size": 100,
                    "browser_download_url": "https://github.com/ollama/ollama/releases/download/v1/"
                    + engine_file,
                }
            ],
        },
    )
    installer = Installer(Store(tmp_path))
    plan = installer.plan()
    assert [p.name for p in plan] == ["Node.js", "OpenClaw", "ollama"]
    assert plan[0].url.endswith(node_file) and plan[2].url.endswith(engine_file)
    assert installer.store.state["plan"][0]["sha256"] == "a" * 64


def test_plan_must_match_reviewed_state(tmp_path):
    installer = Installer(Store(tmp_path))
    with pytest.raises(ForgeError, match="changed"):
        installer.install([])


def test_reuse_does_not_modify_system_executable(tmp_path, monkeypatch):
    from dataclasses import asdict
    from localforge.install import Component

    executable = tmp_path / "external-node"
    executable.write_text("unchanged")
    plan = [Component("Node.js", "v26.13.0", "Reuse", "Existing", existing=str(executable))]
    store = Store(tmp_path / "app")
    store.update(plan=[asdict(p) for p in plan])
    Installer(store).install(plan)
    assert store.state["node"] == str(executable)
    assert executable.read_text() == "unchanged"


@pytest.mark.parametrize(
    "output,minimum,expected",
    [
        ("OpenClaw 2026.11.1 (abc)", "2026.11.1", True),
        ("OpenClaw 2026.10.1", "2026.11.1", False),
        ("Warning: client version is 0.40.15", "0.13.0", True),
        ("unavailable", "0.13.0", False),
    ],
)
def test_runtime_version_floor(output, minimum, expected):
    from localforge.install import version_at_least

    assert version_at_least(output, minimum) is expected
