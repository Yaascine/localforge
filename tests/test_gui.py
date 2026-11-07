from localforge.app import Window
from localforge.core import Store


def test_gui_navigation_and_theme(qtbot, tmp_path):
    window = Window(Store(tmp_path))
    qtbot.addWidget(window)
    window.show()
    assert window.stack.count() == 10
    for index in range(10):
        window.go(index)
        assert window.stack.currentIndex() == index
    window.apply_theme("Light")
    window.apply_theme("Dark")
    assert "Stopped" in window.dashboard.toPlainText()
    window.close()


def test_async_job_returns_on_main_thread(qtbot, tmp_path):
    window = Window(Store(tmp_path))
    qtbot.addWidget(window)
    result = []
    window.task(lambda: 42, result.append)
    qtbot.waitUntil(lambda: not window.busy, timeout=5000)
    assert result == [42]


def test_browse_before_setup_selects_downloadable_file(qtbot, tmp_path, monkeypatch):
    from localforge.models import ModelFile, ModelSource, Repository

    window = Window(Store(tmp_path))
    qtbot.addWidget(window)
    files = [
        ModelFile("weights.safetensors", 100, None, "Unknown", False, "Use GGUF"),
        ModelFile("large.gguf", 32 * 1024**3, "a" * 64, "Q4_K_M", True, ""),
    ]
    repo = Repository(ModelSource("a/b"), "a" * 40, files, "llama", "mit", False, {})
    monkeypatch.setattr("localforge.app.discover", lambda url, token: repo)
    monkeypatch.setattr(window.vault, "get", lambda key: None)
    monkeypatch.setattr(window, "task", lambda work, done: done(work()))
    window.enter_models()
    assert window.stack.currentIndex() == 4
    window.model_url.setText("https://huggingface.co/a/b")
    window.analyze_model()
    assert window.selection().name == "large.gguf"
    assert not window.model_consent.isChecked()
    assert "RAM estimates never block" in window.compat.text()


def test_download_proceeds_when_model_exceeds_ram(qtbot, tmp_path, monkeypatch):
    from localforge.models import GiB, ModelFile, ModelSource, Repository
    from localforge.system import SystemInfo

    store = Store(tmp_path)
    store.update(ollama="test-runtime")
    window = Window(store)
    qtbot.addWidget(window)
    file = ModelFile("large.gguf", 32 * GiB, "a" * 64, "Q4_K_M", True, "")
    repo = Repository(ModelSource("a/b"), "a" * 40, [file], "llama", "mit", False, {})
    window.repository = repo
    monkeypatch.setattr(window, "selection", lambda: file)
    window.model_consent.setChecked(True)
    window.engine.setCurrentText("ollama")
    monkeypatch.setattr(window.vault, "get", lambda key: None)
    monkeypatch.setattr(
        "localforge.app.detect",
        lambda *a, **k: SystemInfo("Darwin", "", "arm64", "CPU", 8, 8 * GiB, GiB, 100 * GiB, 200 * GiB),
    )
    fetched = []

    def fetch(url, target, *args):
        fetched.append(url)
        return target

    monkeypatch.setattr(window.downloader, "fetch", fetch)
    monkeypatch.setattr(window.runtime, "activate", lambda *a: {"inference": True})
    monkeypatch.setattr(window, "task", lambda work, done: done(work()))
    window.download_model()
    assert fetched == [repo.download_url(file)]
    assert store.state["downloaded"]["filename"] == "large.gguf"
