"""Explicit, opt-in real installation/model verification. Never run in default unit tests."""

import argparse
import json
from pathlib import Path

from localforge.core import Store
from localforge.download import Downloader
from localforge.install import Installer
from localforge.models import discover
from localforge.runtime import Runtime

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, default=Path(".test-runtime"))
parser.add_argument("--url", required=True)
parser.add_argument("--filename", required=True)
parser.add_argument("--engine", choices=["ollama", "llama.cpp"], default="ollama")
parser.add_argument(
    "--install", action="store_true", help="Approve installing the resolved official runtime plan into --root"
)
parser.add_argument("--report", type=Path, default=Path("docs/live-report.json"))
args = parser.parse_args()
store = Store(args.root)
installer = Installer(store, print)
runtime = Runtime(store, log=print)
try:
    if args.install:
        plan = installer.plan(args.engine)
        for component in plan:
            print(component)
        installer.install(plan)
    print(runtime.stage())
    repo = discover(args.url)
    file = next(f for f in repo.files if f.name == args.filename)
    if not file.supported:
        raise RuntimeError(file.reason)
    path = Downloader().fetch(
        repo.download_url(file), store.root / "models" / (file.sha256 + ".gguf"), file.sha256, file.size
    )
    report = runtime.activate(path, file.sha256, args.engine, 16384)
    report["versions"] = {
        key: store.state.get(key)
        for key in ("installed_Node.js", "installed_OpenClaw", "installed_ollama", "installed_llama.cpp")
    }
    report["test_model"] = {
        "repo": repo.source.repo,
        "revision": repo.revision,
        "file": file.name,
        "sha256": file.sha256,
    }
    args.report.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))
    if not report["inference"]:
        raise SystemExit(1)
finally:
    runtime.stop()
