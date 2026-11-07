# LocalForge

A setup-and-launch script for OpenClaw with a local GGUF model, plus an optional desktop setup manager. The script opens OpenClaw’s actual browser interface after checking a real model reply.

**Release status: engineering preview, not a production-certified universal installer.** macOS ARM64 is the locally exercised platform. Windows, Intel macOS and Linux have implementation/build targets but require native validation. The full acceptance checklist in the request is not yet satisfied. See [validation and limitations](docs/VALIDATION.md).

![LocalForge welcome screen](docs/welcome.png)

## Install the macOS application icon

After the model setup, stop the running setup terminal with Ctrl+C and quit LocalForge. Run:

```sh
curl -fL https://github.com/Yaascine/localforge/releases/download/v0.1.4/Setup-OpenClaw.command -o "$HOME/Setup-OpenClaw.command" && bash "$HOME/Setup-OpenClaw.command" --install-app
```

This creates **~/Applications/OpenClaw Local.app** and opens it. Keep it in the Dock for future launches. Clicking the icon starts Ollama, loads the saved local model, starts OpenClaw, and embeds OpenClaw's real Control UI in its own desktop window. No Terminal or browser tab is needed for later launches. Closing the window stops its managed runtimes. The launcher uses its private installed Python environment; retain the setup cache in `~/.local/share/localforge-setup/0.1.4`. This is a LocalForge desktop wrapper around real OpenClaw, not an official OpenClaw desktop distribution. The icon installer currently supports macOS only.

## Set up and open OpenClaw (recommended)

Quit the LocalForge desktop app first. On your Mac, paste this into Terminal:

```sh
curl -fL https://github.com/Yaascine/localforge/releases/download/v0.1.3/Setup-OpenClaw.command -o "$HOME/Setup-OpenClaw.command" && bash "$HOME/Setup-OpenClaw.command"
```

The script installs a private Python environment, installs/reuses Node.js, OpenClaw and your selected local inference backend, reuses your downloaded model, configures OpenClaw, checks a real agent reply, and opens **OpenClaw's own authenticated browser chat interface**. No LocalForge chat panel or DMG is involved. First-time users without a saved model are asked for a Hugging Face URL and file selection. Invoking the setup script authorizes the displayed runtime installation plan.

Keep the terminal open while chatting. Press Ctrl+C to stop. Next time run:

```sh
bash "$HOME/Setup-OpenClaw.command"
```

To choose a different model, pass `--model-url URL --file FILENAME.gguf`. Your existing data is retained. The script uses the same LocalForge application-data directory and does not overwrite another OpenClaw profile. It preserves the existing restricted tool policy; full shell/computer control is not enabled. A model that rejects tools is retried in OpenClaw with tools disabled.

**Windows:** download [Setup-OpenClaw.ps1](https://github.com/Yaascine/localforge/releases/download/v0.1.3/Setup-OpenClaw.ps1), then run it in PowerShell:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File "$HOME\Downloads\Setup-OpenClaw.ps1"
```

The policy override applies only to this PowerShell process. Windows and Linux bootstrap paths are provided but not locally validated; the tested host is macOS ARM64. Both scripts verify their pinned uv download and the release source archive before executing them. Model downloads retain published SHA-256 verification. RAM estimates do not gate setup.

[Release notes, scripts, source and checksums](https://github.com/Yaascine/localforge/releases/tag/v0.1.3)

## Earlier desktop preview

The [0.1.2 Apple Silicon DMG](https://github.com/Yaascine/localforge/releases/download/v0.1.2/LocalForge-0.1.2-macos-arm64.dmg) remains available for the optional setup-manager GUI. Version 0.1.3 is a script release; use the script above for the OpenClaw interface. The earlier DMG is not notarized.

## Run from source

Install Git and uv, then:

```sh
git clone https://github.com/Yaascine/localforge.git
cd localforge
uv sync --frozen --extra dev
uv run localforge
```

Python 3.12+ is required for source execution. Packaged applications include Python and Qt; target AI runtimes are downloaded only after a reviewed plan is approved.

On macOS, open the DMG in `dist/`, drag LocalForge to Applications and launch it. The development build is ad-hoc signed, **not Developer ID signed or notarized**. Do not treat it as an OS-trusted distribution. Signing instructions are in [packaging](docs/PACKAGING.md).

## What works in the application

- A ten-page native setup wizard, light/dark themes, asynchronous installation work, diagnostics and a process dashboard.
- Hardware/OS/RAM/disk probes, GPU/driver discovery, network probes, package-manager and existing-installation detection, and port-conflict checks.
- Reviewable, version-pinned installation plans. Node is downloaded from nodejs.org with SHA-256 verification; OpenClaw is installed into a private npm prefix; Ollama and llama.cpp use digest-verified official release archives.
- A separate OpenClaw state/config/workspace, with schema validation and backup/rollback on invalid configuration. Existing personal OpenClaw configuration is not overwritten.
- Hugging Face model repository/direct file URLs, immutable revisions, file browsing, quantization discovery, license metadata and authenticated access through the OS credential store.
- RAM estimates are advisory: models can be downloaded and loaded even when their estimated working set exceeds available RAM. Loading may still fail inside the selected backend.
- Explicit memory estimates that account for context, KV cache dimensions when supplied, runtime overhead and disk duplication. Missing metadata produces an Unknown result; runtime load tests remain authoritative.
- Streaming downloads, pause/resume, retry, space checks, SHA-256 verification and reuse of verified files. Cross-host redirects do not receive Hugging Face credentials.
- Ollama GGUF registration and native API inference; llama.cpp GGUF loading and OpenAI-compatible API inference. Both run on loopback on dedicated ports.
- Independent inference, OpenClaw and read-tool checks, including recorded evidence from the supported trajectory exporter. An unverified tool action leaves a Limited result and disables tools for that model.
- Telegram/Discord credential validation and strict numeric user allowlists, stored credentials in the OS keychain and outbound-only messaging connections. Live provider behavior still requires account-based validation.
- Start/stop/restart, model switching and previous-model recovery, diagnostic exports, and app-owned runtime removal that preserves models, workspace, configuration and unrelated software.

## Supported model path

Select a **single-file GGUF with published LFS SHA-256 metadata**. The app deliberately does not execute model repository code. Split GGUF, automatic Safetensors/PyTorch conversion, multimodal projector setup and arbitrary remote-code architectures are not supported in this release. They appear as unsupported entries rather than being silently ignored.

Ollama is the default engine. llama.cpp is an explicit alternative with a conservative CPU configuration. The selected engine and model architecture still need a successful runtime load; a `.gguf` extension is not a compatibility guarantee. A minimum 16K context is exposed for OpenClaw; large context windows can consume substantial memory. After download, bounded GGUF metadata parsing caps context to the model native limit. Models below 16K can run inference but are reported as unsuitable for OpenClaw setup.

## Privacy and security boundaries

The installer is not a security sandbox. It runs trusted runtime packages as the current user. npm lifecycle scripts are publisher code, disclosed in the approval screen. No administrator escalation or unverified remote shell script is used. Runtime downloads need network access to Node.js, npm, GitHub and Hugging Face.

OpenClaw gets a dedicated workspace and an explicit tool allowlist containing `read` and `session_status`; shell, editing, browser and network-fetch tools are denied. Workspace-only filesystem access is configured. This reduces the effects of untrusted prompts; it is not a claim to solve prompt injection. Powerful computer-command/task execution and a sensitive-operation approval bridge are not enabled in this release.

Credentials go to the operating-system keychain (macOS Keychain, Windows credential storage, Linux Secret Service). There is no plaintext fallback. Gateway authentication uses a per-launch secret injected through the child environment. Local model APIs are loopback-only. llama.cpp uses an ephemeral API key; Ollama has no local API authentication, so **other processes running locally can access Ollama**. No public-network binding is exposed.

The application manages only its own child processes. Closing it stops its services. Persistent login services are not installed. Private state lives under the platform's `LocalForge` user-data directory, displayed in Settings.

## Development and packaging

```sh
uv run pytest -q
uv run ruff check src tests packaging --select F
uv run python packaging/build.py
```

The build script produces a native macOS `.dmg`, Windows Inno Setup `.exe`, or Debian/Ubuntu `.deb` on the corresponding host. CI defines native build/test jobs; a workflow definition is not evidence those jobs have run. See [architecture](docs/ARCHITECTURE.md), [research](docs/RESEARCH.md), [packaging](docs/PACKAGING.md), and [validation](docs/VALIDATION.md).

Runtime versions are resolved at plan creation, displayed for approval and recorded in state. The desktop dependency graph is locked in `uv.lock`. Runtime dependencies and platform SDKs are not bit-for-bit reproducible; release artifacts need independent signing and native certification.
