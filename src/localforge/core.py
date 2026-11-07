from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable

import keyring
from platformdirs import user_data_path


_known_secrets: set[str] = set()


def protect_secret(value: str | None):
    if value and len(value) >= 6:
        _known_secrets.add(value)


class ForgeError(Exception):
    """An actionable error safe to display after redaction."""


class Paused(ForgeError):
    pass


def redact(text: str) -> str:
    text = str(text)
    for secret in tuple(_known_secrets):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"hf_[A-Za-z0-9]+", "[REDACTED]", str(text))
    text = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{20,}", "[REDACTED]", text)
    return re.sub(
        r'(?i)(authorization|token|api[_-]?key|password)(["\s:=]+)([^\s,"}]+)', r"\1\2[REDACTED]", text
    )


def atomic_json(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


class Store:
    def __init__(self, root: Path | None = None):
        self.root = (root or user_data_path("LocalForge", appauthor=False)).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        for part in ("models", "runtime", "logs", "workspace", "openclaw", "downloads"):
            (self.root / part).mkdir(exist_ok=True, mode=0o700)
        self.path = self.root / "state.json"
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        marker = self.root / ".localforge-owned"
        marker.touch(mode=0o600, exist_ok=True)

    def update(self, **values):
        self.state.update(values)
        atomic_json(self.path, self.state)

    def log(self, message):
        from datetime import datetime, timezone

        path = self.root / "logs" / "installer.log"
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            path.replace(path.with_suffix(".previous.log"))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(datetime.now(timezone.utc).isoformat() + " " + redact(message) + "\n")


class Vault:
    service = "LocalForge"

    def get(self, name):
        try:
            value = keyring.get_password(self.service, name)
            protect_secret(value)
            return value
        except Exception as exc:
            raise ForgeError(
                "OS credential storage is unavailable. Unlock your keychain or secret service."
            ) from exc

    def set(self, name, value):
        protect_secret(value)
        backend = keyring.get_keyring()
        if backend.priority < 1 or "plaintext" in type(backend).__name__.lower():
            raise ForgeError("A secure OS keychain is required; plaintext credential storage is refused.")
        try:
            keyring.set_password(self.service, name, value)
        except Exception as exc:
            raise ForgeError("Could not save to the OS keychain. Unlock it and retry.") from exc

    def delete(self, name):
        try:
            keyring.delete_password(self.service, name)
        except keyring.errors.PasswordDeleteError:
            pass


def run(args: list[str], *, env=None, timeout=120, log: Callable = lambda _: None, cwd=None) -> str:
    """No shell interpolation, bounded lifetime, no credentials in command logging."""
    if not args or any("\x00" in str(a) for a in args):
        raise ForgeError("Invalid command arguments.")
    import psutil

    process = None
    try:
        process = subprocess.Popen(
            [str(a) for a in args],
            env=env,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if process is not None:
            try:
                parent = psutil.Process(process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    child.kill()
                parent.kill()
                psutil.wait_procs([parent] + children, timeout=5)
            except psutil.NoSuchProcess:
                pass
            process.communicate(timeout=5)
        raise ForgeError(
            f"{Path(args[0]).name} timed out. Its owned process tree was stopped; retry the operation."
        ) from exc
    except OSError as exc:
        raise ForgeError(f"{Path(args[0]).name} could not start: {redact(str(exc))}") from exc
    output = stdout + stderr
    log(redact(output[-16000:]))
    if process.returncode:
        raise ForgeError(f"{Path(args[0]).name} exited with {process.returncode}: {redact(output[-3000:])}")
    return stdout.strip() or stderr.strip()
