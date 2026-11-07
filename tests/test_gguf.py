import struct

import pytest

from localforge.core import ForgeError
from localforge.gguf import metadata


def string(text):
    value = text.encode()
    return struct.pack("<Q", len(value)) + value


def test_metadata_without_loading_tensors(tmp_path):
    header = b"GGUF" + struct.pack("<IQQ", 3, 0, 2)
    header += string("general.architecture") + struct.pack("<I", 8) + string("llama")
    header += string("llama.context_length") + struct.pack("<II", 4, 8192)
    path = tmp_path / "model.gguf"
    path.write_bytes(header)
    assert metadata(path) == {"general.architecture": "llama", "llama.context_length": 8192}


@pytest.mark.parametrize(
    "data",
    [
        b"not gguf",
        b"GGUF" + struct.pack("<IQQ", 3, 0, 999999999),
        b"GGUF" + struct.pack("<IQQQ", 3, 0, 1, 2**63),
    ],
)
def test_malformed_metadata_bounded(tmp_path, data):
    path = tmp_path / "bad.gguf"
    path.write_bytes(data)
    with pytest.raises(ForgeError):
        metadata(path)
