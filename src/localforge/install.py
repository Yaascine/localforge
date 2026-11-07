from __future__ import annotations

import os
import platform
import re
import shutil
import tarfile
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
from packaging.version import Version

from .core import ForgeError, Store, run
from .download import Downloader


@dataclass
class Component:
    name: str
    version: str
    action: str
    source: str
    url: str = ""
    sha256: str = ""
    size: int = 0
    existing: str = ""


def node_compatible(value: str) -> bool:
    try:
        v = Version(value.strip().lstrip("v"))
        return (v.major == 24 and v >= Version("24.16.0")) or (v.major >= 26 and v >= Version("26.1.0"))
    except Exception:
        return False


def version_at_least(output: str, minimum: str) -> bool:
    match = re.search(r"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)", output)
    return bool(match and Version(match[1]) >= Version(minimum))


def extract(archive: Path, target: Path):
    """Reject traversal and archive links rather than allowing writes outside staging."""
    target.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as z:
            for m in z.infolist():
                p = (target / m.filename).resolve()
                if not p.is_relative_to(target.resolve()) or (m.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ForgeError("Unsafe archive member.")
            z.extractall(target)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                if (
                    not (target / m.name).resolve().is_relative_to(target.resolve())
                    or m.isdev()
                    or m.isfifo()
                ):
                    raise ForgeError("Unsafe archive member.")
                if m.issym() or m.islnk():
                    # Node archives use relative internal npm links; validate both symlink and hardlink targets.
                    base = (target / m.name).parent if m.issym() else target
                    if not (base / m.linkname).resolve().is_relative_to(target.resolve()):
                        raise ForgeError("Unsafe archive link.")
            t.extractall(target, filter="data")


class Installer:
    def __init__(self, store: Store, log=lambda _: None):
        self.store, self.log = store, log
        self.download = Downloader()

    def node(self):
        p = self.store.state.get("node")
        return p if p and Path(p).is_file() else shutil.which("node")

    def env(self):
        env = os.environ.copy()
        if self.node():
            env["PATH"] = str(Path(self.node()).parent) + os.pathsep + env.get("PATH", "")
        return env

    def claw(self):
        path = self.store.state.get("openclaw")
        if path and Path(path).is_file():
            return [self.node(), path] if path.endswith((".mjs", ".js")) else [path]
        existing = shutil.which("openclaw")
        if existing:
            return [existing]
        raise ForgeError("Install OpenClaw first.")

    def plan(self, engine="ollama") -> list[Component]:
        if engine not in ("ollama", "llama.cpp"):
            raise ForgeError("Unsupported inference engine.")
        system, machine = platform.system(), platform.machine().lower()
        arch = {"arm64": "arm64", "aarch64": "arm64", "amd64": "x64", "x86_64": "x64"}.get(machine)
        if system not in ("Darwin", "Linux", "Windows") or not arch:
            raise ForgeError(f"No verified installer for {system}/{machine}.")
        plan = []
        with httpx.Client(timeout=30, follow_redirects=True) as c:
            node = self.node()
            version = run([node, "--version"]) if node else ""
            if node and node_compatible(version):
                plan.append(Component("Node.js", version, "Reuse", "Existing installation", existing=node))
            else:
                r = c.get("https://nodejs.org/dist/index.json")
                r.raise_for_status()
                release = next((v for v in r.json() if v.get("lts") and node_compatible(v["version"])), None)
                if not release:
                    raise ForgeError("No supported Node LTS release found.")
                version = release["version"]
                os_tag = {"Darwin": "darwin", "Linux": "linux", "Windows": "win"}[system]
                filename = f"node-{version}-{os_tag}-{arch}." + ("zip" if system == "Windows" else "tar.gz")
                base = f"https://nodejs.org/dist/{version}/"
                sums = c.get(base + "SHASUMS256.txt")
                sums.raise_for_status()
                sha = next(
                    (line.split()[0] for line in sums.text.splitlines() if line.split()[-1] == filename), None
                )
                if not sha:
                    raise ForgeError("Node did not publish a checksum for this platform.")
                plan.append(
                    Component(
                        "Node.js", version, "Install privately", base + "SHASUMS256.txt", base + filename, sha
                    )
                )
            existing = self.store.state.get("openclaw") or shutil.which("openclaw")
            if existing and not Path(existing).is_file():
                existing = None
            if existing:
                args = [node, existing] if str(existing).endswith((".js", ".mjs")) and node else [existing]
                try:
                    version = run(args + ["--version"], env=self.env(), timeout=30)
                    if not version_at_least(version, "2026.11.1"):
                        raise ForgeError("Existing OpenClaw predates the supported configuration/export API.")
                    plan.append(
                        Component(
                            "OpenClaw",
                            version,
                            "Reuse",
                            "Existing installation; isolated app configuration",
                            existing=existing,
                        )
                    )
                except ForgeError:
                    existing = None
            if not existing:
                r = c.get("https://registry.npmjs.org/openclaw/latest")
                r.raise_for_status()
                data = r.json()
                version = data["version"]
                if not re.fullmatch(r"\d+[.\dA-Za-z-]*", version):
                    raise ForgeError("Invalid OpenClaw package version.")
                plan.append(
                    Component(
                        "OpenClaw",
                        version,
                        "Install privately",
                        "https://registry.npmjs.org/openclaw (npm integrity verification)",
                    )
                )
            binary_name = "ollama" if engine == "ollama" else "llama-server"
            binary = self.store.state.get(engine) or shutil.which(binary_name)
            if binary and Path(binary).is_file():
                try:
                    runtime_version = run([binary, "--version"], timeout=20)
                    if engine == "ollama" and not version_at_least(runtime_version, "0.13.0"):
                        binary = None
                    if engine == "llama.cpp":
                        help_text = run([binary, "--help"], timeout=20)
                        if not all(
                            flag in help_text for flag in ("--jinja", "--alias", "--parallel", "--api-key")
                        ):
                            binary = None
                except ForgeError:
                    binary = None
            else:
                binary = None
            if binary:
                plan.append(
                    Component(engine, runtime_version, "Reuse", "Existing installation", existing=binary)
                )
            else:
                repo = "ollama/ollama" if engine == "ollama" else "ggml-org/llama.cpp"
                r = c.get(f"https://api.github.com/repos/{repo}/releases/latest")
                r.raise_for_status()
                release = r.json()
                if engine == "ollama":
                    wanted = (
                        "ollama-darwin.tgz"
                        if system == "Darwin"
                        else f"ollama-linux-{'amd64' if arch == 'x64' else 'arm64'}.tgz"
                        if system == "Linux"
                        else f"ollama-windows-{'amd64' if arch == 'x64' else 'arm64'}.zip"
                    )
                    candidates = [a for a in release["assets"] if a["name"] == wanted]
                    if not candidates and system == "Linux":
                        candidates = [
                            a for a in release["assets"] if a["name"] == wanted.replace(".tgz", ".tar.zst")
                        ]
                else:
                    tag = (
                        ("macos-arm64" if arch == "arm64" else "macos-x64")
                        if system == "Darwin"
                        else ("ubuntu-arm64" if arch == "arm64" else "ubuntu-x64")
                        if system == "Linux"
                        else ("win-cpu-arm64" if arch == "arm64" else "win-cpu-x64")
                    )
                    candidates = [
                        a
                        for a in release["assets"]
                        if tag in a["name"] and a["name"].endswith((".zip", ".tar.gz"))
                    ]
                if not candidates and engine == "llama.cpp":
                    # llama.cpp's /latest may be a source-only semantic release.
                    # Resolve the newest published binary release, then pin its tag and digest.
                    r = c.get(f"https://api.github.com/repos/{repo}/releases", params={"per_page": 10})
                    r.raise_for_status()
                    for binary_release in r.json():
                        if binary_release.get("draft"):
                            continue
                        candidates = [
                            a
                            for a in binary_release["assets"]
                            if tag in a["name"] and a["name"].endswith((".zip", ".tar.gz"))
                        ]
                        if candidates:
                            release = binary_release
                            break
                if not candidates:
                    raise ForgeError(
                        f"No supported {engine} release archive for this platform. Install the official runtime and retry."
                    )
                asset = candidates[0]
                sha = (asset.get("digest") or "").removeprefix("sha256:")
                if not re.fullmatch("[0-9a-f]{64}", sha):
                    raise ForgeError(
                        f"{engine} release has no published SHA-256. Install the signed official package and retry."
                    )
                plan.append(
                    Component(
                        engine,
                        release["tag_name"],
                        "Install privately",
                        f"https://github.com/{repo}/releases",
                        asset["browser_download_url"],
                        sha,
                        asset["size"],
                    )
                )
        self.store.update(plan=[asdict(x) for x in plan], planned_engine=engine)
        return plan

    def install(self, plan: list[Component], progress=lambda *_: None):
        if [asdict(x) for x in plan] != self.store.state.get("plan"):
            raise ForgeError("Installation plan changed. Review it again.")
        for index, comp in enumerate(plan):
            self.log(f"{comp.action}: {comp.name} {comp.version}")
            self.store.update(install_stage=comp.name, install_error=None)
            try:
                if comp.action == "Reuse":
                    key = "node" if comp.name == "Node.js" else comp.name.lower()
                    self.store.update(**{key: comp.existing})
                    continue
                if comp.name == "OpenClaw":
                    node = Path(self.node())
                    npm = (
                        node.parent / "node_modules/npm/bin/npm-cli.js"
                        if os.name == "nt"
                        else node.parent.parent / "lib/node_modules/npm/bin/npm-cli.js"
                    )
                    if not npm.exists():
                        npm_bin = shutil.which("npm", path=self.env()["PATH"])
                        if not npm_bin:
                            raise ForgeError(
                                "Node is installed but npm is missing. Repair the Node installation."
                            )
                        npm = Path(npm_bin).resolve()
                    prefix = self.store.root / "runtime" / "openclaw"
                    args = [
                        str(node),
                        str(npm),
                        "install",
                        "--global",
                        "--prefix",
                        str(prefix),
                        "--registry=https://registry.npmjs.org",
                        "--no-audit",
                        "--no-fund",
                        f"openclaw@{comp.version}",
                    ]
                    npm_version = run([str(node), str(npm), "--version"], env=self.env())
                    if Version(npm_version) >= Version("11.16.0"):
                        args.append("--allow-scripts=openclaw")
                    run(args, env=self.env(), timeout=1800, log=self.log)
                    matches = list(prefix.rglob("openclaw.mjs"))
                    if not matches:
                        raise ForgeError("OpenClaw package installed but CLI entry point was not found.")
                    run([str(node), str(matches[0]), "--version"], env=self.env(), timeout=60, log=self.log)
                    self.store.update(openclaw=str(matches[0]))
                else:
                    archive = self.store.root / "downloads" / comp.url.rsplit("/", 1)[-1]
                    self.download.fetch(comp.url, archive, comp.sha256, comp.size, progress=progress)
                    with tempfile.TemporaryDirectory(dir=self.store.root / "runtime") as tmp:
                        staging = Path(tmp)
                        if archive.name.endswith(".zst"):
                            import zstandard

                            unpacked = staging / "archive.tar"
                            with archive.open("rb") as inp, unpacked.open("wb") as out:
                                zstandard.ZstdDecompressor().copy_stream(inp, out)
                            extract(unpacked, staging / "contents")
                            unpacked.unlink()
                            contents = staging / "contents"
                        else:
                            extract(archive, staging / "contents")
                            contents = staging / "contents"
                        target = self.store.root / "runtime" / f"{comp.name.lower()}-{comp.version}"
                        if target.exists():
                            shutil.rmtree(target)  # Only an app-owned, versioned runtime directory.
                        shutil.move(str(contents), target)
                    binary = (
                        "node"
                        if comp.name == "Node.js"
                        else "ollama"
                        if comp.name == "ollama"
                        else "llama-server"
                    )
                    matches = list(target.rglob(binary + (".exe" if os.name == "nt" else "")))
                    if not matches:
                        raise ForgeError(f"{binary} executable missing from release.")
                    executable = matches[0]
                    if os.name != "nt":
                        executable.chmod(executable.stat().st_mode | 0o755)
                    run([str(executable), "--version"], timeout=30, log=self.log)
                    self.store.update(**{"node" if comp.name == "Node.js" else comp.name: str(executable)})
                self.store.update(**{f"installed_{comp.name}": comp.version})
            except Exception as exc:
                self.store.update(install_error=str(exc))
                raise
        self.store.update(install_stage="Infrastructure installed; model not configured")
