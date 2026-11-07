import hashlib

import httpx
import pytest
import respx

from localforge.core import ForgeError, Paused, atomic_json
from localforge.download import Downloader

URL = "https://huggingface.co/a/b/resolve/" + "a" * 40 + "/model.gguf"
DATA = b"GGUF" + b"hello" * 100
SHA = hashlib.sha256(DATA).hexdigest()


@respx.mock
def test_verified_download_and_reuse(tmp_path):
    route = respx.get(URL).mock(return_value=httpx.Response(200, content=DATA))
    path = tmp_path / "model.gguf"
    assert Downloader().fetch(URL, path, SHA, len(DATA)).read_bytes() == DATA
    Downloader().fetch(URL, path, SHA, len(DATA))
    assert route.call_count == 1


@respx.mock
def test_resume(tmp_path):
    path = tmp_path / "model.gguf"
    path.with_name(path.name + ".part").write_bytes(DATA[:50])
    atomic_json(path.with_name(path.name + ".download.json"), {"url": URL, "sha256": SHA, "size": len(DATA)})

    def respond(request):
        assert request.headers["Range"] == "bytes=50-"
        return httpx.Response(
            206, headers={"Content-Range": f"bytes 50-{len(DATA) - 1}/{len(DATA)}"}, content=DATA[50:]
        )

    respx.get(URL).mock(side_effect=respond)
    assert Downloader().fetch(URL, path, SHA, len(DATA)).read_bytes() == DATA


@respx.mock
def test_range_ignored_restarts_safely(tmp_path):
    path = tmp_path / "m"
    path.with_name("m.part").write_bytes(DATA[:50])
    atomic_json(path.with_name("m.download.json"), {"url": URL, "sha256": SHA, "size": len(DATA)})
    respx.get(URL).mock(return_value=httpx.Response(200, content=DATA))
    assert Downloader().fetch(URL, path, SHA, len(DATA)).read_bytes() == DATA


@respx.mock
def test_corrupt_download_removed(tmp_path):
    respx.get(URL).mock(return_value=httpx.Response(200, content=b"x" * len(DATA)))
    with pytest.raises(ForgeError, match="SHA-256"):
        Downloader().fetch(URL, tmp_path / "m", SHA, len(DATA))
    assert not (tmp_path / "m.part").exists()


def test_pause_before_request(tmp_path):
    downloader = Downloader()
    downloader.pause.set()
    with pytest.raises(Paused):
        downloader.fetch(URL, tmp_path / "m", SHA, len(DATA))


@respx.mock
def test_bad_content_range(tmp_path):
    respx.get(URL).mock(
        return_value=httpx.Response(206, headers={"Content-Range": f"bytes 9-99/{len(DATA)}"}, content=DATA)
    )
    with pytest.raises(ForgeError, match="range"):
        Downloader().fetch(URL, tmp_path / "m", SHA, len(DATA))


def test_source_mismatch(tmp_path):
    (tmp_path / "m.part").write_bytes(b"old")
    with pytest.raises(ForgeError, match="another source"):
        Downloader().fetch(URL, tmp_path / "m", SHA, len(DATA))


@respx.mock
def test_token_not_forwarded_to_cdn(tmp_path):
    respx.get(URL).mock(
        return_value=httpx.Response(302, headers={"Location": "https://cdn.example.test/file"})
    )

    def respond(request):
        assert "authorization" not in request.headers
        return httpx.Response(200, content=DATA)

    respx.get("https://cdn.example.test/file").mock(side_effect=respond)
    Downloader().fetch(URL, tmp_path / "m", SHA, len(DATA), token="hf_secret")


@respx.mock
def test_no_http_redirect(tmp_path):
    respx.get(URL).mock(
        return_value=httpx.Response(302, headers={"Location": "http://cdn.example.test/file"})
    )
    with pytest.raises(ForgeError, match="insecure"):
        Downloader().fetch(URL, tmp_path / "m", SHA, len(DATA))


def test_disk_space(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr("localforge.download.shutil.disk_usage", lambda _: SimpleNamespace(free=1))
    with pytest.raises(ForgeError, match="disk space"):
        Downloader().fetch(URL, tmp_path / "m", SHA, len(DATA))


@respx.mock
def test_pause_during_stream_preserves_resumable_partial(tmp_path):
    data = b"GGUF" + b"x" * (3 * 1024 * 1024)
    sha = hashlib.sha256(data).hexdigest()
    downloader = Downloader()
    path = tmp_path / "large.gguf"
    respx.get(URL).respond(200, content=data)

    def pause_after_progress(done, total, speed):
        downloader.pause.set()

    with pytest.raises(Paused):
        downloader.fetch(URL, path, sha, len(data), progress=pause_after_progress)
    partial = path.with_name(path.name + ".part")
    assert 0 < partial.stat().st_size < len(data)
    assert not path.exists()
