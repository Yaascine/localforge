#!/bin/bash
# Installs missing prerequisites, reuses your model, and opens OpenClaw itself.
set -euo pipefail
VERSION=0.1.4
BASE="https://github.com/Yaascine/localforge/releases/download/v$VERSION"
CACHE="${XDG_DATA_HOME:-$HOME/.local/share}/localforge-setup/$VERSION"
mkdir -p "$CACHE"
fetch() { curl --fail --location --retry 3 --proto '=https' --tlsv1.2 "$1" -o "$2"; }
sha() { if command -v shasum >/dev/null; then shasum -a 256 "$1" | awk '{print $1}'; else sha256sum "$1" | awk '{print $1}'; fi; }
case "$(uname -s)-$(uname -m)" in
 Darwin-arm64) TARGET=aarch64-apple-darwin; HASH=7e6ddb9316acc00f2296c82ff4d99977870ee34b2f0ddcae9444d714db9364ed ;;
 Darwin-x86_64) TARGET=x86_64-apple-darwin; HASH=5e287ef61cb6a9b61b3a83fef124fd143e400468a7dac794230147a810e17119 ;;
 Linux-x86_64) TARGET=x86_64-unknown-linux-gnu; HASH=745765a3b6e360ad76743599ae5c42e9278c7edf8bbff9fc76d05bf2623a04dd ;;
 Linux-aarch64) TARGET=aarch64-unknown-linux-gnu; HASH=2eaa5d94f5db7b3a1a092156b9420459e42ab0217d917fe74a876309cef9b5e9 ;;
 *) echo 'Unsupported platform.'; exit 1 ;;
esac
UV="$CACHE/uv-$TARGET/uv"
if [ ! -x "$UV" ]; then
 echo 'Installing private Python bootstrap (uv)…'
 fetch "https://github.com/astral-sh/uv/releases/download/0.12.13/uv-$TARGET.tar.gz" "$CACHE/uv.tar.gz"
 [ "$(sha "$CACHE/uv.tar.gz")" = "$HASH" ] || { echo 'uv checksum mismatch'; exit 1; }
 tar -xzf "$CACHE/uv.tar.gz" -C "$CACHE"
fi
if [ ! -f "$CACHE/LocalForge/uv.lock" ]; then
 ARCHIVE="LocalForge-$VERSION-source.zip"
 fetch "$BASE/$ARCHIVE" "$CACHE/source.zip"
 fetch "$BASE/SHA256SUMS.txt" "$CACHE/SHA256SUMS.txt"
 EXPECTED=$(awk -v name="$ARCHIVE" '$2 == name {print $1}' "$CACHE/SHA256SUMS.txt")
 [ -n "$EXPECTED" ] && [ "$(sha "$CACHE/source.zip")" = "$EXPECTED" ] || { echo 'Source checksum mismatch'; exit 1; }
 # uv supplies Python even when this computer has no Python installation.
 "$UV" run --no-project --python 3.12 python -m zipfile -e "$CACHE/source.zip" "$CACHE"
fi
cd "$CACHE/LocalForge"
echo 'Starting OpenClaw setup. Close the LocalForge desktop app first.'
exec "$UV" run --frozen --python 3.12 python -m localforge.setup_openclaw "$@"
