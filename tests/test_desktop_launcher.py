import plistlib
from pathlib import Path
from localforge.desktop import install_launcher


def test_macos_launcher_points_to_installed_python(tmp_path, monkeypatch):
    monkeypatch.setattr('localforge.desktop.sys.platform', 'darwin')
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    bundle = install_launcher()
    info = plistlib.loads((bundle / 'Contents/Info.plist').read_bytes())
    assert info['CFBundleExecutable'] == 'OpenClaw Local'
    script = bundle / 'Contents/MacOS/OpenClaw Local'
    assert '-m localforge.desktop' in script.read_text()
    assert script.stat().st_mode & 0o111
