"""Bounded GGUF metadata reader. Never reads tensor payloads or executes model code."""

from __future__ import annotations

import struct
from pathlib import Path

from .core import ForgeError


def metadata(path: Path) -> dict:
    limits = min(path.stat().st_size, 64 * 1024 * 1024)
    scalars = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}
    with path.open("rb") as file:

        def read(n):
            if n < 0 or file.tell() + n > limits:
                raise ForgeError("GGUF metadata exceeds safe bounds or is truncated.")
            data = file.read(n)
            if len(data) != n:
                raise ForgeError("GGUF metadata is truncated.")
            return data

        def integer(fmt):
            return struct.unpack("<" + fmt, read(struct.calcsize("<" + fmt)))[0]

        def string(keep=True):
            length = integer("Q")
            if length > limits or file.tell() + length > limits:
                raise ForgeError("Invalid GGUF string length.")
            if keep and length <= 1024 * 1024:
                return read(length).decode("utf-8", errors="replace")
            file.seek(length, 1)
            return None

        def value(kind, keep=True, depth=0):
            if kind in scalars:
                return integer(scalars[kind])
            if kind == 8:
                return string(keep)
            if kind == 9:
                if depth >= 3:
                    raise ForgeError("GGUF metadata nesting is too deep.")
                element, count = integer("I"), integer("Q")
                if count > 2_000_000:
                    raise ForgeError("GGUF array is too large to inspect safely.")
                if element in scalars:
                    length = struct.calcsize("<" + scalars[element]) * count
                    if file.tell() + length > limits:
                        raise ForgeError("GGUF array exceeds metadata bounds.")
                    file.seek(length, 1)
                else:
                    for _ in range(count):
                        value(element, False, depth + 1)
                return None
            raise ForgeError("Unknown GGUF metadata value type.")

        if read(4) != b"GGUF" or integer("I") not in (2, 3):
            raise ForgeError("This file is not a supported GGUF v2/v3 model.")
        integer("Q")  # Tensor count; tensor data is never parsed here.
        count = integer("Q")
        if count > 100_000:
            raise ForgeError("GGUF has too many metadata fields.")
        result = {}
        for _ in range(count):
            key = string()
            if key is None:
                raise ForgeError("Invalid GGUF metadata key.")
            item = value(integer("I"))
            if item is not None:
                result[key] = item
        return result
