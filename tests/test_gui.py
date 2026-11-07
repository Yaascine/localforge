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
