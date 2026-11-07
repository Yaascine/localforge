"""Build native packages on the host platform (never cross-claim platform validation)."""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
# Retain license notices in every native bundle.
from importlib.metadata import distributions

notices = ROOT / "build/third-party-licenses"
notices.mkdir(parents=True, exist_ok=True)
for distribution in distributions():
    name = distribution.metadata.get("Name", "unknown")
    for member in distribution.files or []:
        if any(word in str(member).lower() for word in ("license", "copying")):
            source = Path(distribution.locate_file(member))
            if source.is_file():
                destination = notices / name / source.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
shutil.copytree(ROOT / "packaging/licenses", notices / "Qt", dirs_exist_ok=True)
shutil.copy2(ROOT / "LICENSE", notices / "LocalForge-LICENSE")
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--windowed",
        "--name",
        "LocalForge",
        "--add-data",
        str(notices) + os.pathsep + "licenses",
        "--collect-all",
        "keyring",
        "--hidden-import",
        "keyring.backends.macOS",
        "--hidden-import",
        "keyring.backends.Windows",
        "--hidden-import",
        "keyring.backends.SecretService",
        "--osx-bundle-identifier",
        "dev.localforge.installer",
        "packaging/entry.py",
    ],
    check=True,
)
dist = ROOT / "dist"
system = platform.system()
if system == "Darwin":
    stage = ROOT / "build/dmg"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    shutil.copytree(dist / "LocalForge.app", stage / "LocalForge.app", symlinks=True)
    (stage / "Applications").symlink_to("/Applications")
    output = dist / f"LocalForge-0.1.0-macos-{platform.machine()}.dmg"
    output.unlink(missing_ok=True)
    subprocess.run(
        [
            "hdiutil",
            "create",
            "-volname",
            "LocalForge",
            "-srcfolder",
            str(stage),
            "-ov",
            "-format",
            "UDZO",
            str(output),
        ],
        check=True,
    )
elif system == "Windows":
    compiler = shutil.which("iscc") or str(
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Inno Setup 6" / "ISCC.exe"
    )
    if not Path(compiler).is_file():
        raise RuntimeError("Install Inno Setup 6 or add iscc to PATH before packaging Windows.")
    subprocess.run([compiler, "packaging/windows.iss"], check=True)
elif system == "Linux":
    arch = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    stage = ROOT / "build/deb"
    shutil.rmtree(stage, ignore_errors=True)
    app = stage / "opt/localforge"
    app.parent.mkdir(parents=True)
    shutil.copytree(dist / "LocalForge", app)
    (stage / "DEBIAN").mkdir()
    (stage / "DEBIAN/control").write_text(f"""Package: localforge
Version: 0.1.0
Section: utils
Priority: optional
Architecture: {arch}
Maintainer: LocalForge contributors
Depends: libegl1, libgl1, libxcb-cursor0, libxkbcommon-x11-0, libdbus-1-3
Description: Local AI environment installer
""")
    apps = stage / "usr/share/applications"
    apps.mkdir(parents=True)
    (apps / "localforge.desktop").write_text("""[Desktop Entry]
Name=LocalForge
Exec=/opt/localforge/LocalForge
Type=Application
Categories=Utility;
Terminal=false
""")
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(stage), str(dist / f"localforge_0.1.0_{arch}.deb")],
        check=True,
    )
