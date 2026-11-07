# Architecture

LocalForge uses a single language, Python 3.12+, and Qt through PySide6. This gives a native desktop event loop, mature packaging, accessibility semantics and native OS credential backends without a browser server or Rust/JavaScript bridge.

## Modules

| Module | Responsibility |
|---|---|
| `app.py` | Native wizard, worker serialization, progress events, settings, diagnostics |
| `core.py` | Atomic private state, command execution, credential vault, redaction |
| `system.py` | Measured machine facts and explicit unknowns |
| `install.py` | Official-source resolution, versioned plans, safe archive extraction, private installs |
| `models.py` | Strict URL parsing, immutable repository metadata, format filtering, memory estimates |
| `download.py` | Streaming/range downloads, retries, disk reservation, SHA-256 verification |
| `runtime.py` | Owned child lifecycle, engine adapters, config transactions, integration tests, messaging |

The UI serializes mutating jobs on a QThreadPool worker. Qt signals marshal progress and results back to the GUI thread. A process lock prevents two GUI instances managing the same installation. Persistent JSON writes use atomic replacement. Interrupted downloads keep a sidecar binding partial bytes to the immutable URL, expected size and digest.

## Runtime isolation

Node, npm-prefix OpenClaw and downloaded inference engines live below the application runtime directory. Existing compatible executables can be reused without modifying their configuration. OpenClaw receives explicit state and configuration paths; Ollama receives a private model-cache path and port. Gateway credentials are injected per launch. Ports are checked before spawning; services that the app did not create are never adopted or stopped.

Default ports: Ollama 11435, llama.cpp 8081, OpenClaw 18790, all bound to 127.0.0.1. There is an unavoidable bind-check/start race; startup health checks catch collisions rather than claiming readiness. Port selection is not yet configurable.

## Readiness

1. Infrastructure installation and configuration validation.
2. User approves one model artifact.
3. Verified model bytes are downloaded.
4. Engine registers/loads the model and returns nonempty generated text.
5. OpenClaw gateway starts and health CLI succeeds.
6. An agent turn asks the read tool to retrieve a randomly generated workspace-only fixture.
7. Returned fixture content plus a completed read result in the CLI-exported trajectory is required before tool verification is marked successful.

A text reply alone never establishes tool support. Small integration-test models may generate text while failing tool verification. The dashboard distinguishes live child-process state from the previous verification report.

## Security limits

The host/user is trusted. Private directory permissions and configuration backups are not a defense against arbitrary malware already running as that user. OS-level container isolation and a generalized privilege broker are not implemented. The initial restricted agent cannot run shell commands, modify files or browse. Messaging follows upstream access policy; live unauthorized-user rejection must be checked with real accounts before deployment.

Ollama also initializes its standard signing-key files below the user's `.ollama` directory on first launch. LocalForge does not replace the process home directory and does not remove those potentially shared files on uninstall. The approval screen discloses this runtime metadata. No Ollama cloud sign-in is performed.

If tool verification fails, the model is explicitly switched to a no-tools configuration and tested through OpenClaw again. Successful text-only operation remains Limited. Restoring the previous model is an explicit verification-page action; it re-runs verification rather than copying a stale success status.
