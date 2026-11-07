# Native packaging

Run `uv sync --frozen --extra dev`, then `uv run python packaging/build.py` **on each target OS**.

- macOS: PyInstaller creates `LocalForge.app`; `hdiutil` creates a compressed DMG with an Applications shortcut. ARM64 and Intel builds require their own native Python/runtime. The current local artifact targets ARM64. It is ad-hoc signed, not signed with a Developer ID and not notarized. For distribution, use a valid Developer ID identity, hardened-runtime entitlements appropriate for Python/Qt, sign nested libraries and the app, submit with `xcrun notarytool`, staple the ticket, rebuild/sign the DMG, and test Gatekeeper on a fresh machine. This repository contains no signing credentials.
- Windows: PyInstaller plus Inno Setup 6 (`iscc` on PATH). The installer uses the current-user Programs folder and no elevation. Sign the EXE and installer with Authenticode using your own certificate. SmartScreen reputation is not established by generating a package.
- Linux: PyInstaller plus `dpkg-deb`; the package declares core Qt platform dependencies. Build on the oldest supported glibc baseline and test on each declared distribution. Fedora/RPM and AppImage packaging are not supplied in this release.

The GitHub Actions matrix includes macOS ARM64, macOS Intel, Ubuntu and Windows. It runs mocked integration and offscreen Qt tests before building packages. It does not provision messaging accounts, download large models, or demonstrate GPU drivers. Workflow execution and artifact signing remain release duties.

Desktop dependencies are locked in `uv.lock`. PyInstaller bundles Python, PySide6/Qt and supporting libraries. Third-party notices and license obligations must accompany redistributed builds; PySide6/Qt are LGPLv3/GPLv3/commercial licensed. The application dynamically loads bundled Qt libraries, which can be replaced with ABI-compatible builds. Do not add distribution restrictions that prevent lawful replacement or reverse engineering for LGPL debugging. See [Qt licensing](https://doc.qt.io/qtforpython-6/licenses.html) and the installed distributions' license files. Runtime packages and model weights retain their own licenses.
