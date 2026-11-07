"""Install/reuse a local model and open the real OpenClaw Control UI."""

from __future__ import annotations

import argparse
import json
import signal
import secrets
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PySide6.QtCore import QCoreApplication, QLockFile

from .core import ForgeError, Store, Vault, redact, run
from .download import Downloader
from .install import Installer
from .models import discover
from .runtime import Runtime


def choose_model(store, url=None, filename=None, engine=None):
    if not url:
        for key in ("downloaded", "candidate", "active_model"):
            saved = store.state.get(key)
            if saved and Path(saved["path"]).is_file():
                return {**saved, "engine": engine or saved["engine"]}
        url = input("Paste your Hugging Face model URL: ").strip()
    try:
        token = Vault().get("huggingface")
    except ForgeError:
        token = None
    repo = discover(url, token)
    files = [f for f in repo.files if f.supported]
    if not files:
        raise ForgeError("No supported single-file GGUF weights in this repository.")
    wanted = filename or repo.source.file
    if wanted:
        selected = next((f for f in files if f.name == wanted), None)
        if selected is None:
            raise ForgeError("The requested file is not a supported main-model GGUF.")
    else:
        for i, file in enumerate(files, 1):
            print(f"{i}. {file.name} ({file.size / 1024**3:.2f} GiB)")
        choice = input("Choose a file number: ").strip()
        if not choice.isdigit() or not 1 <= int(choice) <= len(files):
            raise ForgeError("Choose one of the listed file numbers.")
        selected = files[int(choice) - 1]
    print(f"Downloading {selected.name}; existing verified downloads will be reused.", flush=True)
    path = Downloader().fetch(
        repo.download_url(selected),
        store.root / "models" / (selected.sha256 + ".gguf"),
        selected.sha256,
        selected.size,
        token,
    )
    result = {
        "path": str(path),
        "sha256": selected.sha256,
        "engine": engine or "ollama",
        "context": 16384,
        "repository": repo.source.repo,
        "filename": selected.name,
    }
    store.update(downloaded=result)
    return result


def prepare(runtime, model, context=None):
    """Exercise the real gateway/model route before handing its UI to the user."""
    runtime.stop()
    workspace = runtime.store.root / "workspace"
    instructions = workspace / "AGENTS.md"
    if not instructions.exists():
        instructions.write_text(
            "# Local assistant workspace\n\nAnswer the user's requests clearly. Use only tools permitted by the gateway configuration.\n",
            encoding="utf-8",
        )
    candidate = runtime.register(
        Path(model["path"]), model["sha256"], model["engine"], context or model.get("context", 16384)
    )
    if candidate["context"] < 16384:
        raise ForgeError(
            "This model's native context is below OpenClaw's 16K minimum. Choose a model with at least 16K context."
        )
    # Retain the existing workspace-only read policy. A read-tool benchmark is not a chat prerequisite.
    config = runtime.config(candidate)
    config["agents"]["defaults"]["thinkingDefault"] = "off"
    config["agents"]["defaults"]["timeoutSeconds"] = 1800
    runtime.write_config(config)
    runtime.start_gateway()
    try:
        reply = runtime.agent_turn(
            "Say hello in one short sentence.", "localforge-setup-" + secrets.token_hex(8), timeout=1800
        )
    except ForgeError:
        print("Retrying OpenClaw chat without model tool calls…", flush=True)
        runtime.stop("openclaw")
        candidate["tools_enabled"] = False
        config = runtime.config(candidate)
        config["agents"]["defaults"].update(thinkingDefault="off", timeoutSeconds=1800)
        runtime.write_config(config)
        runtime.start_gateway()
        reply = runtime.agent_turn(
            "Say hello in one short sentence.", "localforge-setup-text-" + secrets.token_hex(8), timeout=1800
        )
    if not reply.strip():
        raise ForgeError(
            "OpenClaw returned no final answer. The model remains installed; inspect the runtime log and retry."
        )
    runtime.store.update(
        candidate=candidate,
        active_model=candidate,
        verification={"inference": True, "gateway": True, "agent": True, "tools": False},
        agent_status="OpenClaw chat ready; tools not verified by this setup run",
    )
    result = json.loads(
        run(runtime.installer.claw() + ["dashboard", "--json"], env=runtime.env(), timeout=90)
    )
    browser_url = result.get("browserUrl")
    parsed = urlsplit(browser_url or "")
    if (
        result.get("ok") is False
        or parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.port != runtime.gateway_port
    ):
        raise ForgeError("OpenClaw did not provide a valid authenticated local dashboard link.")
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(f"http://127.0.0.1:{runtime.gateway_port}/")
        response.raise_for_status()
        if "<html" not in response.text.lower():
            raise ForgeError("OpenClaw's browser interface is not available.")
    return browser_url, reply


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, help="Optional separate installation; default reuses LocalForge data"
    )
    parser.add_argument("--model-url")
    parser.add_argument("--file")
    parser.add_argument("--engine", choices=["ollama", "llama.cpp"], default="ollama")
    parser.add_argument("--context", type=int)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run the real readiness checks then stop without opening a browser",
    )
    parser.add_argument("--install-app", action="store_true")
    args = parser.parse_args(argv)
    if args.install_app:
        from .desktop import install_launcher
        import subprocess

        bundle = install_launcher()
        print("Installed application: " + str(bundle))
        subprocess.run(["open", str(bundle)], check=True)
        return 0
    app = QCoreApplication.instance() or QCoreApplication([])
    store = Store(args.root)
    lock = QLockFile(str(store.root / "application.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        print("Close LocalForge or the other setup terminal, then run this script again.", flush=True)
        return 1
    runtime = Runtime(store, log=store.log)
    try:
        print("Setting up the real OpenClaw interface. Runtime logs: " + str(store.root / "logs"), flush=True)
        model = choose_model(store, args.model_url, args.file, args.engine)
        installer = Installer(store, store.log)
        plan = installer.plan(model["engine"])
        for component in plan:
            print(component, flush=True)
        print("Installing missing components and reusing existing ones…", flush=True)
        installer.install(plan)
        print("Loading your model and checking a real OpenClaw reply…", flush=True)
        url, reply = prepare(runtime, model, args.context)
        print("OpenClaw replied: " + reply, flush=True)
        if args.check:
            print("PASS: OpenClaw chat and authenticated dashboard handoff are ready.", flush=True)
            return 0
        if not webbrowser.open(url):
            raise ForgeError("Could not open your browser. Set a default browser and rerun the script.")
        print(
            "OpenClaw is open. Keep this terminal open while using it. Press Ctrl+C to stop. Run this script again to relaunch.",
            flush=True,
        )
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        while not stopped.wait(2):
            if any(p.poll() is not None for p in runtime.processes.values()):
                raise ForgeError("A runtime exited. See the logs above; rerun the script to restart.")
        return 0
    except KeyboardInterrupt:
        print("Stopping your OpenClaw session.")
        return 0
    except Exception as exc:
        print("Setup failed: " + redact(str(exc)), flush=True)
        return 1
    finally:
        runtime.stop()
        lock.unlock()
        del app


if __name__ == "__main__":
    raise SystemExit(main())
