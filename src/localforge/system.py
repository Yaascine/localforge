from __future__ import annotations

import json
import platform
import shutil
import socket
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
import psutil

from .core import run

GiB = 1024**3


def port_free(port: int) -> bool:
    with socket.socket() as s:
        if platform.system() != "Windows":
            # Match server bind semantics: TIME_WAIT is not an active listener.
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


@dataclass
class SystemInfo:
    os: str
    release: str
    arch: str
    cpu: str
    cores: int
    ram_total: int
    ram_available: int
    disk_free: int
    disk_total: int
    gpus: list[dict] = field(default_factory=list)
    software: dict = field(default_factory=dict)
    managers: list[str] = field(default_factory=list)
    ports: dict = field(default_factory=dict)
    network: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def detect(root: Path, network=True) -> SystemInfo:
    mem, disk = psutil.virtual_memory(), shutil.disk_usage(root)
    info = SystemInfo(
        platform.system(),
        platform.release(),
        platform.machine(),
        platform.processor() or platform.machine(),
        psutil.cpu_count(logical=False) or 1,
        mem.total,
        mem.available,
        disk.free,
        disk.total,
    )
    info.managers = [p for p in ("brew", "winget", "apt-get", "dnf", "pacman", "zypper") if shutil.which(p)]
    for name in ("node", "npm", "git", "python3", "ollama", "llama-server", "openclaw", "wsl"):
        p = shutil.which(name)
        if p:
            info.software[name] = {"path": p}
            if name not in ("npm", "wsl"):
                try:
                    info.software[name]["version"] = run([p, "--version"], timeout=12)[:300]
                except Exception:
                    info.software[name]["version"] = "Could not query version"
    try:
        if info.os == "Darwin":
            info.cpu = run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"])
            displays = json.loads(run(["/usr/sbin/system_profiler", "SPDisplaysDataType", "-json"]))
            for gpu in displays.get("SPDisplaysDataType", []):
                info.gpus.append(
                    {
                        "name": gpu.get("sppci_model", "Unknown"),
                        "memory": "Shared unified RAM"
                        if info.arch == "arm64"
                        else gpu.get("spdisplays_vram", "Unknown"),
                        "acceleration": str(
                            gpu.get("spdisplays_metal")
                            or gpu.get("spdisplays_mtlgpufamilysupport")
                            or "Not reported"
                        ).replace("spdisplays_metal", "Metal "),
                    }
                )
        elif info.os == "Windows":
            raw = run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-Command",
                    "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion | ConvertTo-Json",
                ]
            )
            for g in json.loads(raw) if raw.startswith("[") else [json.loads(raw)]:
                info.gpus.append(
                    {
                        "name": g["Name"],
                        "memory": "Unknown (WMI VRAM is unreliable)",
                        "driver": g.get("DriverVersion"),
                    }
                )
        elif shutil.which("lspci"):
            for line in run(["lspci"]).splitlines():
                if "VGA" in line or "3D controller" in line:
                    info.gpus.append({"name": line, "memory": "Unknown"})
        if shutil.which("nvidia-smi"):
            raw = run(
                [
                    "nvidia-smi",
                    "--query-gpu=name,memory.total,driver_version",
                    "--format=csv,noheader,nounits",
                ]
            )
            info.gpus = [
                {
                    "name": s[0].strip(),
                    "vram_bytes": int(s[1]) * 1024**2,
                    "driver": s[2].strip(),
                    "acceleration": "NVIDIA driver available; runtime test required",
                }
                for s in (line.split(",") for line in raw.splitlines())
            ]
    except Exception as exc:
        info.warnings.append(f"GPU probe incomplete: {exc}")
    if not info.gpus:
        info.warnings.append("GPU acceleration is unverified. CPU inference may still work.")
    if mem.available < 8 * GiB:
        info.warnings.append("Available RAM is limited. Choose a small model and a shorter context.")
    info.ports = {str(p): port_free(p) for p in (11435, 18790, 8081)}
    if network:
        for host in ("huggingface.co", "registry.npmjs.org", "nodejs.org", "api.github.com"):
            try:
                with httpx.Client(timeout=8, follow_redirects=True) as client:
                    response = client.head("https://" + host)
                    info.network[host] = f"HTTP {response.status_code}"
            except httpx.HTTPError:
                info.network[host] = "Unreachable"
    return info
