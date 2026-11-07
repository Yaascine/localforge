from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath
from urllib.parse import quote, unquote, urlsplit

import httpx

from .core import ForgeError
from .system import GiB, SystemInfo


@dataclass(frozen=True)
class ModelSource:
    repo: str
    revision: str = "main"
    file: str | None = None


def safe_filename(value: str) -> str:
    p = PurePosixPath(value)
    if (
        not value
        or p.is_absolute()
        or any(x in ("", ".", "..") for x in value.split("/"))
        or "\\" in value
        or any(ord(c) < 32 for c in value)
    ):
        raise ForgeError("Invalid model file path.")
    return value


def parse_url(url: str) -> ModelSource:
    p = urlsplit(url.strip())
    if (
        p.scheme != "https"
        or p.hostname != "huggingface.co"
        or p.username
        or p.password
        or p.port
        or p.query
        or p.fragment
    ):
        raise ForgeError(
            "Use an HTTPS huggingface.co model repository or resolve/blob file URL, without query parameters."
        )
    parts = unquote(p.path).strip("/").split("/")
    if len(parts) < 2 or any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", x) for x in parts[:2]):
        raise ForgeError("Expected https://huggingface.co/owner/model-name.")
    if parts[0] in ("datasets", "spaces", "api"):
        raise ForgeError("Only model repositories are supported.")
    if len(parts) == 2:
        return ModelSource("/".join(parts))
    if len(parts) >= 4 and parts[2] in ("tree", "blob", "resolve"):
        revision = parts[3]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", revision) or revision in (".", ".."):
            raise ForgeError("Unsupported revision.")
        file = safe_filename("/".join(parts[4:])) if len(parts) > 4 else None
        return ModelSource("/".join(parts[:2]), revision, file)
    raise ForgeError("Unsupported Hugging Face URL path.")


@dataclass
class ModelFile:
    name: str
    size: int
    sha256: str | None
    quantization: str
    supported: bool
    reason: str


@dataclass
class Repository:
    source: ModelSource
    revision: str
    files: list[ModelFile]
    architecture: str
    license: str
    gated: bool
    config: dict

    def download_url(self, file: ModelFile):
        return (
            f"https://huggingface.co/{self.source.repo}/resolve/{self.revision}/{quote(file.name, safe='/')}"
        )


def discover(url: str, token: str | None = None) -> Repository:
    source = parse_url(url)
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=30, headers=headers, follow_redirects=True) as c:
        response = c.get(
            f"https://huggingface.co/api/models/{source.repo}/revision/{quote(source.revision, safe='')}",
            params={"blobs": "true"},
        )
        if response.status_code in (401, 403):
            raise ForgeError(
                "Repository access requires an authorized Hugging Face token and acceptance of any gated license."
            )
        if response.status_code == 404:
            raise ForgeError(
                "Repository or revision not found, or a private repository requires authentication."
            )
        response.raise_for_status()
        data = response.json()
        revision = data.get("sha", "")
        if not re.fullmatch(r"[0-9a-f]{40,64}", revision):
            raise ForgeError("Repository did not provide an immutable revision.")
        config = {}
        # Read only JSON metadata; never import repository code or trust_remote_code.
        if any(f.get("rfilename") == "config.json" for f in data.get("siblings", [])):
            with c.stream("GET", f"https://huggingface.co/{source.repo}/resolve/{revision}/config.json") as r:
                if r.status_code == 200:
                    raw = b""
                    for chunk in r.iter_bytes():
                        raw += chunk
                        if len(raw) > 2 * 1024 * 1024:
                            raise ForgeError("Repository config is unexpectedly large.")
                    import json

                    config = json.loads(raw)
        files = []
        for f in data.get("siblings", []):
            name = safe_filename(f["rfilename"])
            if not name.lower().endswith((".gguf", ".safetensors", ".bin")):
                continue
            lfs = f.get("lfs") or {}
            sha = lfs.get("sha256")
            sha = sha if sha and re.fullmatch("[0-9a-f]{64}", sha) else None
            split = bool(re.search(r"-\d{5}-of-\d{5}\.gguf$", name))
            ok = name.lower().endswith(".gguf") and not split and bool(sha)
            reason = (
                "Single-file GGUF; architecture must be validated by the runtime."
                if ok
                else "Split GGUF is not yet supported. Choose a single-file variant."
                if split
                else "No published SHA-256; verified download is unavailable."
                if name.lower().endswith(".gguf")
                else "Use a GGUF conversion from the publisher; automatic conversion is not supported."
            )
            q = re.search(r"(?i)(IQ\d_[A-Z0-9_]+|Q\d(?:_[A-Z0-9]+)+|BF16|F16|F32)", name)
            files.append(
                ModelFile(
                    name,
                    int(lfs.get("size") or f.get("size") or 0),
                    sha,
                    q.group(0).upper() if q else "Unknown",
                    ok,
                    reason,
                )
            )
        card = data.get("cardData") or {}
        return Repository(
            source,
            revision,
            files,
            str(config.get("model_type") or data.get("pipeline_tag") or "Unknown"),
            str(card.get("license") or "Not specified; review repository terms"),
            bool(data.get("gated")),
            config,
        )


def estimate(file: ModelFile, repo: Repository, system: SystemInfo, context=4096) -> dict:
    cfg = repo.config.get("text_config", repo.config)
    layers, heads, kv_heads, hidden = (
        cfg.get(k) for k in ("num_hidden_layers", "num_attention_heads", "num_key_value_heads", "hidden_size")
    )
    known = all(isinstance(x, int) and 0 < x < 100000 for x in (layers, heads, hidden))
    if known:
        kv = 2 * layers * (kv_heads or heads) * (hidden // heads) * context * 2
    else:
        kv = max(
            512 * 1024**2, context * 256 * 1024
        )  # Explicitly an uncertainty allowance, not a claim about architecture.
    low = int(file.size * 1.10 + kv + 768 * 1024**2)
    high = int(file.size * 1.35 + kv * 1.5 + 1536 * 1024**2)
    storage = file.size * 2 + GiB  # Download plus Ollama's content-addressed import.
    status = "Unknown" if not known else "Compatible"
    reasons = [
        f"Estimated working set {low / GiB:.1f}–{high / GiB:.1f} GiB at {context:,} tokens; not a benchmark.",
        "Architecture and runtime support are confirmed only by a load test.",
    ]
    if not known:
        reasons.append("Missing architecture dimensions: KV cache uses a conservative allowance.")
    if not file.supported or not file.size or storage > system.disk_free or low > system.ram_available:
        status = "Incompatible"
        reasons.append(
            "Unsupported file, missing size, insufficient disk space, or estimated minimum exceeds available RAM."
        )
    elif high > system.ram_available * 0.8:
        status = "Limited"
        reasons.append("Reduce context or choose a smaller quantization. CPU offload may be slow.")
    return {
        "status": status,
        "memory_low": low,
        "memory_high": high,
        "disk_required": storage,
        "kv_estimate": kv,
        "context": context,
        "reasons": reasons,
    }


def file_dict(file):
    return asdict(file)
