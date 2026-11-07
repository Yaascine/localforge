import httpx
import pytest
import respx

from localforge.core import ForgeError
from localforge.models import ModelFile, ModelSource, Repository, discover, estimate, parse_url, safe_filename
from localforge.system import GiB, SystemInfo


@pytest.mark.parametrize(
    "url",
    [
        "http://huggingface.co/a/b",
        "https://huggingface.co.evil.test/a/b",
        "https://evil.test/a/b",
        "https://user@huggingface.co/a/b",
        "https://huggingface.co:443/a/b",
        "https://huggingface.co/a",
        "https://huggingface.co/datasets/a/b",
        "https://huggingface.co/a/b?token=secret",
        "https://huggingface.co/a/b/resolve/main/../../bad.gguf",
        "https://huggingface.co/a/b/resolve/main/%2e%2e/x.gguf",
    ],
)
def test_reject_urls(url):
    with pytest.raises((ForgeError, ValueError)):
        parse_url(url)


def test_parse_direct_file():
    source = parse_url("https://huggingface.co/owner/model/resolve/abcdef/folder/file.gguf")
    assert source == ModelSource("owner/model", "abcdef", "folder/file.gguf")


@pytest.mark.parametrize("path", ["../x", "/tmp/x", "x/../../y", "x\\y", "a\nx", "a//b"])
def test_path_rejection(path):
    with pytest.raises(ForgeError):
        safe_filename(path)


@respx.mock
def test_repository_discovery():
    sha = "a" * 64
    respx.get("https://huggingface.co/api/models/owner/model/revision/main").mock(
        return_value=httpx.Response(
            200,
            json={
                "sha": "b" * 40,
                "cardData": {"license": "apache-2.0"},
                "siblings": [
                    {"rfilename": "model-Q4_K_M.gguf", "lfs": {"sha256": sha, "size": 1000}},
                    {"rfilename": "model-00001-of-00002.gguf", "lfs": {"sha256": sha, "size": 1000}},
                    {"rfilename": "model.safetensors", "size": 2000},
                    {"rfilename": "mmproj-model-F16.gguf", "lfs": {"sha256": sha, "size": 500}},
                ],
            },
        )
    )
    repo = discover("https://huggingface.co/owner/model")
    assert repo.files[0].quantization == "Q4_K_M"
    assert repo.files[0].supported
    assert not repo.files[1].supported and not repo.files[2].supported
    assert "/resolve/" + ("b" * 40) + "/" in repo.download_url(repo.files[0])
    assert repo.license == "apache-2.0"
    assert not repo.files[3].supported
    assert "projector" in repo.files[3].reason


@respx.mock
def test_private_model():
    respx.get("https://huggingface.co/api/models/a/b/revision/main").mock(return_value=httpx.Response(401))
    with pytest.raises(ForgeError, match="authorized"):
        discover("https://huggingface.co/a/b")


def test_memory_accounts_for_context_and_missing_metadata():
    file = ModelFile("model.gguf", 2 * GiB, "a" * 64, "Q4_K_M", True, "")
    repo = Repository(
        ModelSource("a/b"),
        "a" * 40,
        [file],
        "llama",
        "mit",
        False,
        {"num_hidden_layers": 32, "num_attention_heads": 32, "num_key_value_heads": 8, "hidden_size": 4096},
    )
    sys = SystemInfo("Darwin", "", "arm64", "CPU", 8, 32 * GiB, 24 * GiB, 100 * GiB, 200 * GiB)
    small = estimate(file, repo, sys, 4096)
    large = estimate(file, repo, sys, 32768)
    assert large["memory_low"] > small["memory_low"]
    assert small["status"] == "Compatible"
    repo.config = {}
    assert estimate(file, repo, sys)["status"] == "Unknown"
    sys.disk_free = GiB
    assert estimate(file, repo, sys)["status"] == "Incompatible"


def test_model_larger_than_ram_is_allowed_but_disk_shortage_is_not():
    file = ModelFile("large.gguf", 32 * GiB, "a" * 64, "Q4_K_M", True, "")
    repo = Repository(ModelSource("a/b"), "a" * 40, [file], "llama", "mit", False, {})
    system = SystemInfo("Darwin", "", "arm64", "CPU", 8, 8 * GiB, GiB, 100 * GiB, 200 * GiB)
    report = estimate(file, repo, system)
    assert report["memory_low"] > system.ram_total
    assert report["status"] == "Limited"
    assert "loading are allowed" in " ".join(report["reasons"])
    system.disk_free = GiB
    assert estimate(file, repo, system)["status"] == "Incompatible"


def test_shared_model_url_ignores_tracking_parameters():
    assert parse_url(
        "https://huggingface.co/gguf/Qwen3.5-4B-GGUF/tree/main?utm_source=chatgpt.com"
    ) == ModelSource("gguf/Qwen3.5-4B-GGUF")
    assert parse_url("https://huggingface.co/a/b/resolve/main/model.gguf?download=true") == ModelSource(
        "a/b", "main", "model.gguf"
    )
