"""A desktop window hosting the locally installed OpenClaw Control UI."""

import plistlib
import shlex
import sys
from pathlib import Path

from PySide6.QtCore import QLockFile, QThread, QUrl, Signal
from PySide6.QtWidgets import QApplication, QLabel, QMainWindow, QMessageBox
from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView

from .core import Store, redact
from .runtime import Runtime
from .setup_openclaw import choose_model, prepare
from .install import Installer


def install_launcher():
    if sys.platform != "darwin":
        raise RuntimeError("The application-icon installer currently supports macOS.")
    bundle = Path.home() / "Applications" / "OpenClaw Local.app"
    executable = bundle / "Contents/MacOS/OpenClaw Local"
    executable.parent.mkdir(parents=True, exist_ok=True)
    project = Path(__file__).resolve().parents[2]
    executable.write_text(
        "#!/bin/bash\ncd "
        + shlex.quote(str(project))
        + "\nexec "
        + shlex.quote(sys.executable)
        + " -m localforge.desktop\n"
    )
    executable.chmod(0o755)
    with (bundle / "Contents/Info.plist").open("wb") as file:
        plistlib.dump(
            {
                "CFBundleName": "OpenClaw Local",
                "CFBundleDisplayName": "OpenClaw Local",
                "CFBundleIdentifier": "dev.localforge.openclaw-local",
                "CFBundleExecutable": "OpenClaw Local",
                "CFBundlePackageType": "APPL",
                "CFBundleVersion": "0.1.4",
                "NSHighResolutionCapable": True,
            },
            file,
        )
    return bundle


class Startup(QThread):
    ready = Signal(str)
    failed = Signal(str)

    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime

    def run(self):
        try:
            store = self.runtime.store
            if not any(store.state.get(key) for key in ("downloaded", "candidate", "active_model")):
                raise RuntimeError(
                    "Run the setup script once to select and download your model, then reopen this app."
                )
            model = choose_model(store, engine="ollama")
            installer = Installer(store, store.log)
            installer.install(installer.plan("ollama"))
            url, _ = prepare(self.runtime, model)
            self.ready.emit(url)
        except Exception as exc:
            self.failed.emit(redact(str(exc)))


class Window(QMainWindow):
    def __init__(self, store):
        super().__init__()
        self.setWindowTitle("OpenClaw Local")
        self.resize(1200, 850)
        self.runtime = Runtime(store, log=store.log)
        self.message = QLabel("Starting Ollama and OpenClaw…\nYour local model may take a minute to load.")
        self.message.setMargin(30)
        self.setCentralWidget(self.message)
        self.profile = QWebEngineProfile("OpenClawLocal", self)
        self.profile.setPersistentStoragePath(str(store.root / "desktop-browser"))
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(self.profile, self.view))
        self.worker = Startup(self.runtime)
        self.worker.ready.connect(self.show_chat)
        self.worker.failed.connect(self.show_error)
        self.worker.start()

    def show_chat(self, url):
        self.setCentralWidget(self.view)
        self.view.setUrl(QUrl(url))

    def show_error(self, message):
        self.message.setText("OpenClaw could not start:\n\n" + message + "\n\nClose this window and retry.")

    def closeEvent(self, event):
        if self.worker.isRunning():
            QMessageBox.information(
                self, "Starting OpenClaw", "Please wait for startup to finish before closing."
            )
            event.ignore()
            return
        self.runtime.stop()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("OpenClaw Local")
    store = Store()
    lock = QLockFile(str(store.root / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(
            None,
            "OpenClaw is already running",
            "Stop the setup script with Ctrl+C and quit LocalForge, then reopen OpenClaw Local.",
        )
        return 1
    window = Window(store)
    window.show()
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    raise SystemExit(main())
