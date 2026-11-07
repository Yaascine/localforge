from __future__ import annotations

import json
import sys
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QHeaderView,
)

from .core import ForgeError, Store, Vault, redact
from .download import Downloader
from .install import Installer
from .models import discover, estimate
from .runtime import Runtime
from .system import GiB, detect


class Signals(QObject):
    result = Signal(object)
    error = Signal(str)
    finished = Signal()
    log = Signal(str)
    progress = Signal(int, str)


class Job(QRunnable):
    def __init__(self, fn):
        super().__init__()
        self.fn, self.signals = fn, Signals()

    def run(self):
        try:
            self.signals.result.emit(self.fn())
        except Exception as exc:
            self.signals.error.emit(redact(str(exc)))
        finally:
            self.signals.finished.emit()


def label(text, role=None):
    w = QLabel(text)
    w.setTextFormat(Qt.PlainText)
    w.setWordWrap(True)
    if role:
        w.setObjectName(role)
    return w


def button(text, callback, primary=False):
    b = QPushButton(text)
    b.setMinimumHeight(38)
    b.clicked.connect(callback)
    if primary:
        b.setObjectName("primary")
    return b


class Window(QMainWindow):
    def __init__(self, store=None):
        super().__init__()
        self.store = store or Store()
        self.vault = Vault()
        self.events = Signals()
        self.events.log.connect(self.append_log)
        self.events.progress.connect(self.progress_changed)
        self.installer = Installer(self.store, self.events.log.emit)
        self.runtime = Runtime(self.store, self.vault, self.events.log.emit)
        self.downloader = Downloader()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.busy, self.job, self.system, self.repository, self.plan_data = False, None, None, None, None
        self.setWindowTitle("LocalForge — Your local AI, ready to work")
        self.resize(1160, 800)
        self.setMinimumSize(850, 620)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(235)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(23, 30, 20, 24)
        side.addWidget(label("◈  LocalForge", "brand"))
        side.addWidget(label("YOUR AI. YOUR COMPUTER.", "eyebrow"))
        side.addSpacing(30)
        self.nav = QListWidget()
        self.nav.setObjectName("navigation")
        self.nav.addItems(
            [
                "01   Welcome",
                "02   System check",
                "03   Installation plan",
                "04   Install & configure",
                "05   Choose your model",
                "06   Verification",
                "07   Remote access",
                "08   Dashboard",
                "        Settings",
                "        Diagnostics",
            ]
        )
        side.addWidget(self.nav)
        side.addWidget(label("LOCAL BY DEFAULT\nNo cloud model account required.", "muted"))
        side.addSpacing(12)
        side.addWidget(label("Preview 0.1 · platform validation in progress", "muted"))
        layout.addWidget(sidebar)
        content = QWidget()
        box = QVBoxLayout(content)
        box.setContentsMargins(32, 25, 32, 20)
        self.status = label("Start with a system check. Nothing is installed without your approval.", "muted")
        box.addWidget(self.status)
        self.stack = QStackedWidget()
        box.addWidget(self.stack, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setVisible(False)
        box.addWidget(self.progress)
        layout.addWidget(content, 1)
        self.build_pages()
        self.nav.currentRowChanged.connect(self.navigate)
        self.nav.setCurrentRow(0)
        self.apply_theme(self.store.state.get("theme", "Dark"))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh_dashboard)
        self.timer.start(5000)

    def page(self, title, subtitle):
        page = QWidget()
        outer = QVBoxLayout(page)
        outer.setContentsMargins(0, 16, 0, 0)
        outer.addWidget(label(title, "title"))
        outer.addWidget(label(subtitle, "subtitle"))
        outer.addSpacing(18)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        body = QVBoxLayout(inner)
        body.setContentsMargins(0, 0, 12, 0)
        body.setSpacing(16)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.stack.addWidget(page)
        return body

    def row(self, body, *buttons):
        row = QHBoxLayout()
        for b in buttons:
            row.addWidget(b)
        row.addStretch()
        body.addLayout(row)

    def build_pages(self):
        body = self.page(
            "A capable assistant.\nA computer you control.",
            "Install the foundations, bring your model, and keep inference on your machine.",
        )
        for title, text in [
            (
                "01  Check your computer",
                "Inspect memory, storage, available acceleration and existing software.",
            ),
            (
                "02  Prepare the environment",
                "Review exact versions and sources. LocalForge installs into its own private directory.",
            ),
            (
                "03  Bring your own model",
                "Paste a Hugging Face link after installation. Compare files, approve one, and verify it.",
            ),
            (
                "04  Verify the whole connection",
                "Test inference, OpenClaw and a restricted read tool independently.",
            ),
        ]:
            card = QFrame()
            card.setObjectName("card")
            c = QVBoxLayout(card)
            c.setContentsMargins(18, 16, 18, 16)
            c.addWidget(label(title, "cardTitle"))
            c.addWidget(label(text, "muted"))
            body.addWidget(card)
        body.addWidget(
            label(
                "Allow several GB for runtimes plus twice the selected model size. Downloads contact Node.js, npm, GitHub and Hugging Face. Model licenses remain your responsibility. Remote messaging is optional."
            )
        )
        self.row(
            body,
            button("Start Setup  →", self.analyze_system, True),
            button("Advanced Settings", lambda: self.go(8)),
        )
        body.addStretch()

        body = self.page(
            "Know what you’re working with.", "Measured system information; no arbitrary compatibility score."
        )
        self.system_text = QTextEdit()
        self.system_text.setReadOnly(True)
        self.system_text.setMinimumHeight(350)
        body.addWidget(self.system_text)
        self.row(
            body,
            button("Analyze again", self.analyze_system),
            button("Build installation plan  →", self.make_plan, True),
        )

        body = self.page(
            "A plan you can inspect.",
            "Exact versions are resolved before approval. Existing system configurations stay separate.",
        )
        self.plan_table = QTableWidget(0, 4)
        self.plan_table.setHorizontalHeaderLabels(["Component", "Version", "Action", "Source"])
        self.plan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.plan_table.horizontalHeader().setStretchLastSection(True)
        self.plan_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.plan_table.setMinimumHeight(280)
        body.addWidget(self.plan_table)
        body.addWidget(
            label(
                "All components run as your current user. npm package installation may run publisher lifecycle scripts. The plan uses official sources and package integrity checks. No downloaded shell installer is executed. Ollama may initialize its standard signing-key files under your home .ollama directory; these shared files are preserved on uninstall."
            )
        )
        self.plan_consent = QCheckBox("I approve the components, versions and sources shown above.")
        body.addWidget(self.plan_consent)
        self.row(
            body,
            button("Refresh plan", self.make_plan),
            button("Install approved plan  →", self.install_plan, True),
        )
        body.addStretch()

        body = self.page(
            "Building your local environment.",
            "You can follow the real installation output below. Failed steps can be retried.",
        )
        self.install_log = QTextEdit()
        self.install_log.setReadOnly(True)
        self.install_log.setMinimumHeight(360)
        body.addWidget(self.install_log)
        self.row(
            body,
            button("Retry approved installation", self.install_plan),
            button("Pause package download", lambda: self.installer.download.pause.set()),
            button("Choose a model  →", self.enter_models, True),
        )

        body = self.page(
            "Bring the model you want.",
            "Paste a Hugging Face model repository link. No model is preselected.",
        )
        self.model_url = QLineEdit()
        self.model_url.setPlaceholderText("https://huggingface.co/organization/model-name")
        body.addWidget(label("Enter Hugging Face Model URL", "cardTitle"))
        body.addWidget(self.model_url)
        self.row(
            body,
            button("Analyze Model", self.analyze_model, True),
            button("Browse Available Files", self.analyze_model),
            button("Back", lambda: self.go(3)),
        )
        self.repo_info = label(
            "Repository metadata is untrusted data. Only verified single-file GGUF downloads are currently supported."
        )
        body.addWidget(self.repo_info)
        self.files = QTableWidget(0, 4)
        self.files.setHorizontalHeaderLabels(["File", "Size (GiB)", "Quantization", "Support"])
        self.files.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.files.setSelectionBehavior(QTableWidget.SelectRows)
        self.files.setSelectionMode(QTableWidget.SingleSelection)
        self.files.setEditTriggers(QTableWidget.NoEditTriggers)
        self.files.setMinimumHeight(230)
        self.files.itemSelectionChanged.connect(self.file_selected)
        body.addWidget(self.files)
        self.compat = label("Select a file to inspect memory and storage estimates.")
        body.addWidget(self.compat)
        self.model_consent = QCheckBox("I reviewed the license and approve downloading this exact file.")
        body.addWidget(self.model_consent)
        self.row(
            body,
            button("Download & verify  →", self.download_model, True),
            button("Pause download", self.pause_download),
        )

        body = self.page(
            "Prove that it works.",
            "A successful download is only the beginning. Each capability is verified separately.",
        )
        self.verify_text = QTextEdit()
        self.verify_text.setReadOnly(True)
        self.verify_text.setMinimumHeight(350)
        body.addWidget(self.verify_text)
        self.row(
            body,
            button("Retry runtime verification", self.retry_verify, True),
            button(
                "Restore previous model",
                lambda: self.task(self.runtime.restore_previous, self.show_verification),
            ),
            button("Optional remote access", lambda: self.go(6)),
            button("Open dashboard", lambda: self.go(7)),
        )

        body = self.page(
            "Remote access, on your terms.",
            "Optional. Your gateway stays on loopback; messaging connects outward to the provider.",
        )
        body.addWidget(
            label(
                "Create a Telegram bot with @BotFather, or a Discord bot in the Discord Developer Portal. For Discord, enable the Message Content intent and install the bot. Enter only user IDs you authorize. Groups are disabled; the initial tool policy allows workspace reads and session status only."
            )
        )
        form = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItems(["telegram", "discord"])
        self.bot_token = QLineEdit()
        self.bot_token.setEchoMode(QLineEdit.Password)
        self.allowed_users = QLineEdit()
        self.allowed_users.setPlaceholderText("123456789, 987654321")
        form.addRow("Provider", self.provider)
        form.addRow("Bot credential", self.bot_token)
        form.addRow("Allowed user IDs", self.allowed_users)
        body.addLayout(form)
        body.addWidget(
            label(
                "Saving verifies the bot credential with the selected provider and stores it in the OS keychain. To activate messaging, restart the gateway. A live authorized-user/unauthorized-user test is still required. WhatsApp is not implemented in this release."
            )
        )
        self.row(
            body,
            button("Authenticate & save allowlist", self.save_messaging, True),
            button("Probe connection", self.probe_channels),
            button("Skip", lambda: self.go(7)),
        )
        self.messaging_result = label("Not configured.")
        body.addWidget(self.messaging_result)
        body.addStretch()

        body = self.page(
            "Your local control room.",
            "Live process status and measured memory. Verification results are labeled as last-run results.",
        )
        self.dashboard = QTextEdit()
        self.dashboard.setReadOnly(True)
        self.dashboard.setMinimumHeight(350)
        body.addWidget(self.dashboard)
        self.row(
            body,
            button("Start", lambda: self.task(self.runtime.start, self.show_dashboard), True),
            button("Stop", lambda: self.task(self.runtime.stop, self.show_dashboard)),
            button("Restart", self.restart),
            button("Change Model", self.enter_models),
        )
        self.row(
            body,
            button("Diagnostics", lambda: self.go(9)),
            button("Settings", lambda: self.go(8)),
            button("Uninstall runtimes", self.uninstall),
        )

        body = self.page(
            "Make it fit your computer.", "Changes apply to the next installation plan or model verification."
        )
        form = QFormLayout()
        self.engine = QComboBox()
        self.engine.addItems(["ollama", "llama.cpp"])
        self.engine.setCurrentText(self.store.state.get("engine", "ollama"))
        self.context = QSpinBox()
        self.context.setRange(16384, 131072)
        self.context.setSingleStep(4096)
        self.context.setValue(self.store.state.get("context", 16384))
        self.theme = QComboBox()
        self.theme.addItems(["Dark", "Light"])
        self.theme.setCurrentText(self.store.state.get("theme", "Dark"))
        self.hf_token = QLineEdit()
        self.hf_token.setEchoMode(QLineEdit.Password)
        self.hf_token.setPlaceholderText("Optional; stored in OS keychain")
        form.addRow("Inference backend", self.engine)
        form.addRow("Context tokens", self.context)
        form.addRow("Appearance", self.theme)
        form.addRow("Hugging Face token", self.hf_token)
        body.addLayout(form)
        body.addWidget(
            label(
                "Ollama is recommended for automatic hardware acceleration and native OpenClaw tool transport. llama.cpp uses a portable CPU configuration here. Architecture support is determined by the actual load test. OpenClaw needs at least a 16K context; use smaller quantizations when memory is limited."
            )
        )
        body.addWidget(label("Application data\n" + str(self.store.root), "muted"))
        body.addWidget(
            label(
                "Runtime services are app-managed and stop when you quit. Login/background service installation and custom data-directory relocation are not implemented in this release."
            )
        )
        self.row(
            body,
            button("Save settings", self.save_settings, True),
            button("Remove Hugging Face credential", self.remove_token),
        )
        body.addStretch()

        body = self.page(
            "Understand and recover.",
            "Logs redact common credential formats. Review any diagnostic export before sharing it.",
        )
        self.diagnostics = QTextEdit()
        self.diagnostics.setReadOnly(True)
        self.diagnostics.setMinimumHeight(350)
        body.addWidget(self.diagnostics)
        self.row(
            body,
            button("Refresh diagnostics", self.diagnose, True),
            button("Export report", self.export_report),
            button("Open data folder", self.open_data),
        )

    def go(self, index):
        self.nav.setCurrentRow(index)

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        if index == 7:
            self.refresh_dashboard()
        elif index == 9:
            self.diagnose()

    def task(self, fn, done=lambda _: None):
        if self.busy:
            self.status.setText(
                "An operation is already running. Wait for it to finish, or pause the active download."
            )
            return
        self.busy = True
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText("Working… You can inspect other pages while this operation runs.")
        self.job = Job(fn)
        self.job.signals.result.connect(done)
        self.job.signals.error.connect(self.error)
        self.job.signals.finished.connect(self.finished)
        self.pool.start(self.job)

    def finished(self):
        self.busy = False
        self.progress.setRange(0, 100)
        if self.status.text().startswith("Working"):
            self.status.setText("Operation finished. Review the results below.")
        self.refresh_dashboard()

    def error(self, message):
        self.status.setText(message)
        self.append_log(message)
        QMessageBox.warning(self, "Action needed", message)

    def append_log(self, text):
        text = redact(text)
        self.store.log(text)
        self.install_log.append(text)
        self.install_log.document().setMaximumBlockCount(2000)

    def progress_changed(self, value, text):
        self.progress.setRange(0, 100)
        self.progress.setValue(value)
        self.status.setText(text)

    def progress_callback(self, done, total, speed):
        self.events.progress.emit(
            int(done * 100 / total) if total else 0,
            f"{done / GiB:.2f} / {total / GiB:.2f} GiB · {speed / 1024**2:.1f} MiB/s",
        )

    def analyze_system(self):
        self.go(1)

        def done(info):
            self.system = info
            self.store.update(hardware=info.to_dict())
            rows = [
                f"{info.os} {info.release} · {info.arch}",
                f"CPU: {info.cpu} · {info.cores} physical cores",
                f"RAM: {info.ram_available / GiB:.1f} GiB available / {info.ram_total / GiB:.1f} GiB total",
                f"Storage: {info.disk_free / GiB:.1f} GiB available / {info.disk_total / GiB:.1f} GiB total",
                "\nGPU\n" + json.dumps(info.gpus, indent=2),
                "\nExisting software\n" + json.dumps(info.software, indent=2),
                "\nNetwork\n" + json.dumps(info.network, indent=2),
                "\nPorts (true = available)\n" + json.dumps(info.ports),
                "\nWarnings\n" + "\n".join(info.warnings),
            ]
            self.system_text.setPlainText("\n".join(rows))

        self.task(lambda: detect(self.store.root), done)

    def make_plan(self):
        self.go(2)
        self.plan_consent.setChecked(False)
        engine = self.engine.currentText()

        def done(plan):
            self.plan_data = plan
            self.plan_table.setRowCount(len(plan))
            for i, c in enumerate(plan):
                for j, value in enumerate((c.name, c.version, c.action, c.source)):
                    self.plan_table.setItem(i, j, QTableWidgetItem(value))

        self.task(lambda: self.installer.plan(engine), done)

    def install_plan(self):
        if not self.plan_data or not self.plan_consent.isChecked():
            return self.error("Build and approve an installation plan first.")
        self.go(3)
        self.installer.download.pause.clear()
        plan = list(self.plan_data)

        def work():
            self.runtime.stop()
            self.installer.install(plan, self.progress_callback)
            return self.runtime.stage()

        def done(result):
            self.append_log(result)
            self.status.setText(result)
            self.store.update(infrastructure_ready=True)

        self.task(work, done)

    def enter_models(self):
        if not self.store.state.get("infrastructure_ready"):
            return self.error("Complete installation and validate OpenClaw before choosing a model.")
        self.go(4)

    def analyze_model(self):
        if not self.store.state.get("infrastructure_ready"):
            return self.error("Install and initialize the environment first.")
        url = self.model_url.text()

        def work():
            try:
                token = self.vault.get("huggingface")
            except ForgeError:
                token = None
            return discover(url, token)

        def done(repo):
            self.repository = repo
            self.files.setRowCount(len(repo.files))
            self.model_consent.setChecked(False)
            self.repo_info.setText(
                f"{repo.source.repo} · {repo.architecture}\nLicense: {repo.license}\nRevision: {repo.revision}"
            )
            for i, f in enumerate(repo.files):
                for j, value in enumerate(
                    (f.name, f"{f.size / GiB:.2f}", f.quantization, "Candidate" if f.supported else f.reason)
                ):
                    self.files.setItem(i, j, QTableWidgetItem(value))
                if repo.source.file == f.name:
                    self.files.selectRow(i)
            if not repo.files:
                self.compat.setText(
                    "No supported model weights were found. Choose a GGUF repository from the publisher."
                )

        self.task(work, done)

    def selection(self):
        row = self.files.currentRow()
        if self.repository is None or row < 0:
            raise ForgeError("Select a model file first.")
        return self.repository.files[row]

    def file_selected(self):
        self.model_consent.setChecked(False)
        try:
            f = self.selection()
            if self.system is None:
                self.compat.setText(
                    "Run System check to calculate compatibility. Storage and memory are checked again before download."
                )
                return
            info = self.system
            report = estimate(f, self.repository, info, self.context.value())
            self.compat.setText(
                f"{report['status']} · storage reservation {report['disk_required'] / GiB:.1f} GiB\n"
                + "\n".join(report["reasons"])
                + "\n"
                + f.reason
                + "\nDestination: "
                + str(self.store.root / "models" / (f.sha256 + ".gguf") if f.sha256 else "Unavailable")
            )
        except ForgeError:
            pass

    def download_model(self):
        try:
            f = self.selection()
            if not f.supported or not self.model_consent.isChecked():
                raise ForgeError("Choose a supported file and approve the download.")
        except ForgeError as exc:
            return self.error(str(exc))
        engine = self.engine.currentText()
        context = self.context.value()
        repo = self.repository
        if not self.store.state.get(engine):
            return self.error(
                "The selected backend is not installed. Review and approve a new installation plan in Settings."
            )
        self.downloader.pause.clear()

        def work():
            info = detect(self.store.root, network=False)
            report = estimate(f, repo, info, context)
            if report["status"] == "Incompatible":
                raise ForgeError("\n".join(report["reasons"]))
            try:
                token = self.vault.get("huggingface")
            except ForgeError:
                token = None
            target = self.store.root / "models" / (f.sha256 + ".gguf")
            path = self.downloader.fetch(
                repo.download_url(f), target, f.sha256, f.size, token, self.progress_callback
            )
            self.store.update(
                downloaded={
                    "path": str(path),
                    "sha256": f.sha256,
                    "engine": engine,
                    "context": context,
                    "repository": repo.source.repo,
                    "filename": f.name,
                }
            )
            self.events.log.emit("Model SHA-256 verified. Starting real runtime tests.")
            return self.runtime.activate(path, f.sha256, engine, context)

        self.task(work, self.show_verification)

    def pause_download(self):
        self.downloader.pause.set()

    def show_verification(self, result):
        self.verify_text.setPlainText(json.dumps(result, indent=2))
        self.go(5)
        self.status.setText(
            "Ready: all checks passed."
            if result.get("tools")
            else "Limited: inspect the separate inference, agent and tool results."
        )

    def retry_verify(self):
        model = self.store.state.get("downloaded")
        if not model:
            return self.error("Download and verify a model file first.")
        self.task(
            lambda: self.runtime.activate(
                Path(model["path"]), model["sha256"], model["engine"], model["context"]
            ),
            self.show_verification,
        )

    def show_dashboard(self, _=None):
        self.refresh_dashboard()
        self.go(7)

    def refresh_dashboard(self):
        # Only fast, measured local counters are read on the GUI thread.
        import platform
        import psutil

        mem = psutil.virtual_memory()
        processes = list(self.runtime.processes.items())
        states = {name: ("Running" if proc.poll() is None else "Stopped") for name, proc in processes}
        resident = 0
        for _, proc in processes:
            if proc.poll() is None:
                try:
                    parent = psutil.Process(proc.pid)
                    resident += parent.memory_info().rss
                    resident += sum(child.memory_info().rss for child in parent.children(recursive=True))
                except psutil.Error:
                    pass
        hardware = self.store.state.get("hardware", {})
        cpu = hardware.get("cpu") or platform.processor() or platform.machine()
        gpu = (
            ", ".join(g.get("name", "Unknown") for g in hardware.get("gpus", []))
            or "Run System check to inspect graphics"
        )
        model = self.store.state.get("active_model") or {}
        download = self.store.state.get("downloaded") or {}
        verification = self.store.state.get("verification", {})
        model_name = (
            download.get("filename") if download.get("sha256") == model.get("sha256") else model.get("model")
        )
        lines = [
            "SYSTEM",
            cpu,
            "GPU: " + gpu,
            f"CPU utilization: {psutil.cpu_percent(interval=None):.0f}%",
            f"RAM: {mem.used / GiB:.1f} / {mem.total / GiB:.1f} GiB used",
            f"App-managed runtime memory: {resident / GiB:.2f} GiB resident (GPU allocation excluded)",
            "",
            "MODEL",
            model_name or "No model configured",
            f"Engine: {model.get('engine', 'Not selected')} · Context: {model.get('context', '—')}",
            "",
            "OPENCLAW & SERVICES",
            "OpenClaw: " + ("Installed" if self.store.state.get("openclaw") else "Not installed"),
        ]
        lines.extend([name + ": " + state for name, state in states.items()] or ["Stopped"])
        lines.extend(["", "LAST VERIFICATION — historical results"])
        for key, title in (
            ("inference", "Model generation"),
            ("agent", "OpenClaw response"),
            ("tools", "Restricted read tool"),
        ):
            lines.append(title + ": " + ("Passed" if verification.get(key) else "Not verified"))
        if verification.get("detail"):
            lines.append(verification["detail"])
        lines.extend(["", "REMOTE ACCESS"])
        for provider in ("telegram", "discord"):
            lines.append(
                provider.title()
                + (
                    ": configured; probe connection to verify"
                    if self.store.state.get(provider + "_configured")
                    else ": disabled"
                )
            )
        self.dashboard.setPlainText("\n".join(lines))

    def restart(self):
        def work():
            self.runtime.stop()
            return self.runtime.start()

        self.task(work, self.show_dashboard)

    def save_settings(self):
        if self.busy:
            return self.error("Wait for the active operation before changing settings.")
        try:
            if self.hf_token.text():
                self.vault.set("huggingface", self.hf_token.text().strip())
                self.hf_token.clear()
            self.store.update(
                engine=self.engine.currentText(), context=self.context.value(), theme=self.theme.currentText()
            )
            self.apply_theme(self.theme.currentText())
            self.status.setText("Settings saved. Rebuild the plan if you changed the backend.")
        except Exception as exc:
            self.error(redact(str(exc)))

    def remove_token(self):
        try:
            self.vault.delete("huggingface")
            self.status.setText("Hugging Face credential removed.")
        except Exception as exc:
            self.error(str(exc))

    def save_messaging(self):
        if not self.store.state.get("active_model"):
            return self.error("Configure and verify a local model first.")
        provider, token, users = (
            self.provider.currentText(),
            self.bot_token.text().strip(),
            self.allowed_users.text(),
        )
        if not token:
            return self.error("Enter the bot credential.")

        def done(result):
            self.bot_token.clear()
            self.messaging_result.setText(result)

        self.task(lambda: self.runtime.configure_messaging(provider, token, users), done)

    def probe_channels(self):
        self.task(self.runtime.channel_status, lambda text: self.messaging_result.setText(redact(text)))

    def diagnose(self):
        log = self.store.root / "logs" / "installer.log"
        # Cap the bytes read so a long-running log cannot freeze the GUI.
        if log.exists():
            with log.open("rb") as f:
                f.seek(max(0, log.stat().st_size - 50000))
                text = f.read().decode(errors="replace")
        else:
            text = "No runtime logs yet."
        self.diagnostics.setPlainText(redact(json.dumps(self.store.state, indent=2) + "\n\n" + text))

    def export_report(self):
        self.diagnose()
        path, _ = QFileDialog.getSaveFileName(
            self, "Export diagnostics", "localforge-diagnostics.txt", "Text (*.txt)"
        )
        if path:
            Path(path).write_text(self.diagnostics.toPlainText(), encoding="utf-8")

    def open_data(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.root)))

    def uninstall(self):
        if self.busy:
            return self.error("Wait for the current operation to finish before uninstalling.")
        result = QMessageBox.question(
            self,
            "Remove app-owned runtimes?",
            "Stop services and remove privately installed runtimes and package downloads? Models, your workspace, configuration and logs will be kept. System installations will not be removed.",
        )
        if result == QMessageBox.Yes:

            def work():
                result = self.runtime.uninstall()
                self.store.update(infrastructure_ready=False)
                return result

            self.task(work, lambda text: self.status.setText(text))

    def closeEvent(self, event):
        if self.busy:
            self.downloader.pause.set()
            self.installer.download.pause.set()
            QMessageBox.information(
                self,
                "Operation in progress",
                "Downloads are being paused. Wait for the active operation to finish before closing.",
            )
            event.ignore()
            return
        self.runtime.stop()
        event.accept()

    def apply_theme(self, theme):
        dark = theme == "Dark"
        bg, card, fg, muted, border, side = (
            ("#11191d", "#1b272d", "#e6efed", "#a2b5b4", "#30434a", "#0d1418")
            if dark
            else ("#f4f7f5", "#ffffff", "#162c30", "#536c70", "#cfddda", "#e6eeeb")
        )
        self.setStyleSheet(f"""
            QWidget {{ background: {bg}; color: {fg}; font-family: "Inter", "Segoe UI", "Helvetica Neue"; font-size: 13px; }}
            QWidget#sidebar {{ background: {side}; }}
            QLabel#brand {{ font-size: 24px; font-weight: 700; background: {side}; }}
            QLabel#eyebrow {{ font-size: 9px; letter-spacing: 2px; color: #70baa6; background: {side}; }}
            QLabel#title {{ font-size: 30px; font-weight: 650; }}
            QLabel#subtitle {{ color: {muted}; font-size: 14px; }}
            QLabel#muted {{ color: {muted}; }}
            QLabel#cardTitle {{ font-size: 15px; font-weight: 600; background: transparent; }}
            QFrame#card {{ border: 1px solid {border}; border-radius: 10px; background: {card}; }}
            QFrame#card QLabel {{ background: {card}; }}
            QListWidget#navigation {{ background: {side}; border: 0; outline: 0; }}
            QListWidget#navigation::item {{ padding: 13px 4px; border-radius: 6px; color: {muted}; }}
            QListWidget#navigation::item:selected {{ background: {card}; color: {fg}; }}
            QPushButton {{ background: {card}; border: 1px solid {border}; padding: 8px 14px; border-radius: 6px; font-weight: 600; }}
            QPushButton:hover {{ border-color: #6bbaa3; }}
            QPushButton#primary {{ background: #8cdec0; color: #102820; border-color: #8cdec0; }}
            QLineEdit, QSpinBox, QComboBox {{ background: {card}; border: 1px solid {border}; border-radius: 6px; padding: 9px; }}
            QTextEdit, QTableWidget {{ background: {card}; border: 1px solid {border}; border-radius: 8px; padding: 10px; selection-background-color: #356d60; }}
            QHeaderView::section {{ background: {card}; color: {muted}; padding: 10px; border: 0; }}
            QProgressBar {{ border: 1px solid {border}; border-radius: 5px; height: 16px; text-align: center; }}
            QProgressBar::chunk {{ background: #5cb99c; border-radius: 4px; }}
            QScrollArea {{ border: none; }}
        """)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("LocalForge")
    app.setOrganizationName("LocalForge")
    # One writer per app-data directory, including across processes.
    from PySide6.QtCore import QLockFile

    store = Store()
    lock = QLockFile(str(store.root / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.warning(
            None,
            "LocalForge is already open",
            "Only one LocalForge instance can manage this installation at a time.",
        )
        return 1
    window = Window(store)
    window.show()
    if "--smoke-test" in sys.argv:
        QTimer.singleShot(1500, window.close)
    result = app.exec()
    lock.unlock()
    return result


if __name__ == "__main__":
    sys.exit(main())
