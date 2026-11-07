"""Produce a source-only archive; omit downloaded runtimes, models, credentials and caches."""

import hashlib
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
dist = root / "dist"
dist.mkdir(exist_ok=True)
archive = dist / "LocalForge-0.1.3-source.zip"
allowed = {
    "src",
    "tests",
    "scripts",
    "packaging",
    "docs",
    ".github",
    "README.md",
    "LICENSE",
    "pyproject.toml",
    "uv.lock",
    ".gitignore",
    ".python-version",
}
with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as output:
    paths = []
    for name in sorted(allowed):
        target = root / name
        paths.extend(target.rglob("*") if target.is_dir() else [target])
    for path in sorted(paths):
        rel = path.relative_to(root)
        if rel.parts[0] not in allowed or not path.is_file() or path.is_symlink():
            continue
        if "__pycache__" in rel.parts or path.suffix == ".pyc":
            continue
        info = zipfile.ZipInfo("LocalForge/" + rel.as_posix(), date_time=(2026, 11, 7, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o644 << 16
        output.writestr(info, path.read_bytes())
lines = []
for path in sorted(dist.iterdir()):
    if path.is_file() and (
        ("0.1.3" in path.name and path.suffix in (".dmg", ".exe", ".deb", ".zip"))
        or path.name in ("Setup-OpenClaw.command", "Setup-OpenClaw.ps1")
    ):
        with path.open("rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        lines.append(digest + "  " + path.name)
(dist / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n")
print(archive)
