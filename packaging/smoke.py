"""Launch the built desktop bundle and exercise the Qt event loop, then exit."""

import os
import platform
import subprocess
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
if platform.system() == "Darwin":
    binary = root / "dist/LocalForge.app/Contents/MacOS/LocalForge"
elif platform.system() == "Windows":
    binary = root / "dist/LocalForge/LocalForge.exe"
else:
    binary = root / "dist/LocalForge/LocalForge"
environment = os.environ.copy()
for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
    environment.pop(key, None)
with tempfile.TemporaryDirectory() as working_directory:
    subprocess.run(
        [str(binary), "--smoke-test"], env=environment, cwd=working_directory, check=True, timeout=45
    )
print("Packaged GUI launched and exited successfully.")
