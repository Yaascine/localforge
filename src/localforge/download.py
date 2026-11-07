from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .core import ForgeError, Paused, atomic_json


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


def secure_redirect(response):
    if response.is_redirect:
        target = response.headers.get("location", "")
        if target.startswith("http:"):
            raise ForgeError("Refusing an insecure download redirect.")


class Downloader:
    def __init__(self):
        self.pause = threading.Event()

    def fetch(
        self,
        url: str,
        target: Path,
        expected_sha: str,
        size: int = 0,
        token: str | None = None,
        progress=lambda done, total, speed: None,
    ) -> Path:
        if urlsplit(url).scheme != "https" or not re.fullmatch(r"[a-f0-9]{64}", expected_sha):
            raise ForgeError("HTTPS and a published SHA-256 are required.")
        if token and urlsplit(url).hostname != "huggingface.co":
            raise ForgeError("Hugging Face credentials cannot be sent to this host.")
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        part = target.with_name(target.name + ".part")
        meta = target.with_name(target.name + ".download.json")
        for p in (target, part, meta):
            if p.is_symlink():
                raise ForgeError("Refusing a symbolic-link download destination.")
        if target.exists():
            if digest(target) == expected_sha:
                progress(target.stat().st_size, target.stat().st_size, 0)
                return target
            raise ForgeError("An existing file failed integrity verification. Move it aside before retrying.")
        identity = {"url": url, "sha256": expected_sha, "size": size}
        if part.exists():
            import json

            if not meta.exists() or json.loads(meta.read_text()) != identity:
                raise ForgeError(
                    "Partial download belongs to another source. Remove the partial file before retrying."
                )
        atomic_json(meta, identity)
        headers = {"Accept-Encoding": "identity"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        with httpx.Client(
            timeout=httpx.Timeout(30, read=60),
            follow_redirects=True,
            event_hooks={"response": [secure_redirect]},
        ) as client:
            for attempt in range(4):
                if self.pause.is_set():
                    raise Paused("Download paused. Select Download again to resume.")
                offset = part.stat().st_size if part.exists() else 0
                if size and offset == size:
                    break
                if size and offset > size:
                    raise ForgeError("Partial file is larger than expected; remove it and retry.")
                if size and shutil.disk_usage(target.parent).free < size - offset + 256 * 1024**2:
                    raise ForgeError("Not enough free disk space to finish the download.")
                request_headers = dict(headers)
                if offset:
                    request_headers["Range"] = f"bytes={offset}-"
                try:
                    with client.stream("GET", url, headers=request_headers) as response:
                        if response.status_code in (401, 403):
                            raise ForgeError(
                                "Download authorization failed. Check the token and accept the repository license."
                            )
                        response.raise_for_status()
                        if response.status_code == 206:
                            match = re.fullmatch(
                                r"bytes (\d+)-(\d+)/(\d+)", response.headers.get("Content-Range", "")
                            )
                            if not match or int(match[1]) != offset or (size and int(match[3]) != size):
                                raise ForgeError("The server returned an inconsistent resume range.")
                            total = int(match[3])
                        elif response.status_code == 200:
                            offset = 0
                            total = int(response.headers.get("Content-Length", 0))
                            if size and total and total != size:
                                raise ForgeError("Download size differs from repository metadata.")
                        else:
                            raise ForgeError("Unexpected download response.")
                        total = size or total
                        if not total:
                            raise ForgeError(
                                "Server did not provide a size; cannot safely reserve disk space."
                            )
                        if shutil.disk_usage(target.parent).free < total - offset + 256 * 1024**2:
                            raise ForgeError("Not enough free disk space.")
                        start, initial, last = time.monotonic(), offset, 0.0
                        with part.open("ab" if offset else "wb") as f:
                            for chunk in response.iter_bytes(1024 * 1024):
                                if self.pause.is_set():
                                    raise Paused("Download paused. Select Download again to resume.")
                                offset += len(chunk)
                                if offset > total:
                                    raise ForgeError("Download exceeded its declared size.")
                                f.write(chunk)
                                now = time.monotonic()
                                if now - last > 0.15:
                                    progress(offset, total, (offset - initial) / max(now - start, 0.001))
                                    last = now
                            f.flush()
                            os.fsync(f.fileno())
                        if offset != total:
                            raise httpx.ReadError("Download interrupted before all bytes arrived.")
                        break
                except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code not in (
                        408,
                        429,
                        500,
                        502,
                        503,
                        504,
                    ):
                        raise ForgeError(f"Download returned HTTP {exc.response.status_code}.") from exc
                    if attempt == 3:
                        raise ForgeError(
                            "Download interrupted after retries. Retry to resume the saved partial file."
                        ) from exc
                    if self.pause.wait(2**attempt):
                        raise Paused("Download paused.")
        if digest(part) != expected_sha:
            part.unlink(missing_ok=True)
            raise ForgeError(
                "SHA-256 verification failed. The corrupt download was removed; retry to download again."
            )
        os.replace(part, target)
        meta.unlink(missing_ok=True)
        progress(target.stat().st_size, target.stat().st_size, 0)
        return target
