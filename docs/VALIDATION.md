# Validation record and acceptance gaps

Recorded on **2026-11-07**. This is an engineering preview with a demonstrated macOS path, not a claim of universal production readiness.

## Environment actually exercised

- Apple M4, ARM64, 16 GiB unified RAM; macOS 26.5.2 / Darwin 25.5.0.
- Python 3.12.14, PySide6 6.10.2, PyInstaller 6.19.0.
- Official, privately installed Node v26.13.0 and OpenClaw 2026.11.1.
- Official, SHA-256-verified Ollama v0.40.15 and llama.cpp b12182.
- Native source GUI and packaged macOS app launched. DMG built locally. Windows, Intel macOS and Linux packages were **not built or run here**.

## Automated tests actually run

**82 passed, 1 optional live test skipped** in the default suite. The live repository test was separately enabled and passed against Hugging Face.

Coverage includes strict URL parsing, traversal rejection, private-repository errors, quantization discovery, context/KV memory estimates, GGUF metadata bounds, disk-space failures, verified downloads, Range resumption, servers ignoring Range, corrupt hashes, pause during streaming, credential stripping on CDN redirects, insecure redirects, native installation planning, version floors, archive traversal/symlinks, persisted recovery state, system probing, configuration rollback, inference responses, restricted messaging policies and authentication mocks, credential rollback/redaction, actual owned-file uninstallation, explicit no-tools fallback, exported tool evidence, GUI navigation/themes and worker completion.

Native planning tests for Windows/Linux are mocked. They do not establish that runtime packages execute on those systems. Messaging API tests are mocked. They do not establish actual inbound delivery or unauthorized-user rejection at the provider boundary.

## Real integration results

The models below are **test fixtures only**, not a hardcoded user-facing selection. Production setup accepts the user's repository link.

| Capability | Actual result |
|---|---|
| Hardware detection | Passed on this Mac; Apple M4, unified RAM, storage, graphics and executable probes |
| Node download/install | Passed; official archive and published SHA-256 |
| OpenClaw install/configuration | Passed; private npm prefix, installed CLI schema validation |
| Ollama install | Passed; official release archive and SHA-256 |
| llama.cpp install | Passed; official binary release b12182 and SHA-256 |
| Real Hugging Face discovery/download | Passed for two repositories; immutable revisions, sizes and SHA-256 verified |
| Ollama model generation | Passed with Qwen3-0.6B Q4_K_M |
| Ollama to OpenClaw text | Passed; gateway health and agent response verified |
| Ollama to OpenClaw read tool | Not verified with the test model; tools disabled and a Limited result retained |
| llama.cpp model generation | Passed with the same Qwen3-0.6B Q4_K_M file |
| llama.cpp to OpenClaw text | Passed |
| llama.cpp to OpenClaw read tool | **Passed**; random workspace-file nonce returned, and exported trajectory contained the completed read-tool result |
| llama.cpp API access control | Unauthenticated request rejected with HTTP 401 |
| Reinstall/reuse | Real plan reused all three compatible components; installation repeated without replacing them |
| Start / stop / restart | Real engine and gateway passed health checks, stopped and restarted; owned processes cleaned up |
| Telegram / Discord schemas | Both passed the real OpenClaw validator with dummy credentials and mocked authentication; no live account connected |
| Packaged GUI | Native app launched and exited through its real Qt event loop |

Machine-readable reports: [Ollama](live-qwen-report.json), [llama.cpp](live-llama-report.json). A successful read-tool check proves this path worked; it does not prove a small model will reliably perform arbitrary tasks.

The initial 135M SmolLM2 model generated text but declares an 8K native context. The final app detects and caps that context, and reports that it cannot meet OpenClaw's 16K minimum. Earlier exploratory logs remain only in the ignored development directory.

## Requirements not complete or not certified

| Area | Limitation |
|---|---|
| Production certification | No multi-machine soak, adversarial security audit, upgrade matrix or release signing/notarization |
| Native platforms | macOS ARM64 exercised; other implementations and CI targets unverified locally |
| Windows / WSL2 | Native provisioning implemented; automatic WSL2 distro creation, reboot handling and WSL services not implemented |
| Linux distributions | Debian package build target supplied; RPM/AppImage not supplied; distro/driver/library combinations need testing |
| Model formats | Single-file GGUF only; no split-shard orchestration, automatic conversion, multimodal projectors or executable repository code |
| Estimation | Memory ranges are estimates; GGUF-only repositories often lack pre-download dimensions. Dedicated GPU residency/performance not benchmarked |
| Acceleration | Detection and runtime selection exist; driver installs, AMD ROCm add-ons and automatic llama.cpp GPU build selection not implemented. llama.cpp defaults to CPU |
| Tool reliability | One restricted read test implemented; no general tool reliability suite |
| Computer actions | Shell execution, destructive operations, arbitrary edits and a general sensitive-action approval broker are not enabled |
| Messaging | Telegram/Discord config and credentials implemented; real authentication, authorized control, unauthorized rejection, files and notifications require accounts/live tests. WhatsApp not implemented |
| Credentials | OS keychain integration has no plaintext fallback; real credential roundtrip on each OS not certified |
| Background services | App-owned children only; login services, tray lifetime and autostart not implemented |
| Storage settings | Data location displayed; arbitrary relocation/model-directory migration not implemented |
| API boundaries | llama.cpp uses an ephemeral key. Ollama is loopback-only but unauthenticated; local processes can access it |
| Reproducibility | Desktop dependencies locked; toolchains, npm transitive dependencies and signatures are not bit-for-bit reproducible |

## Reproduce the real check

`scripts/live_smoke.py` accepts an explicit URL, filename, engine and isolated root. `--install` authorizes its development runtime plan. Default tests never download a model or install AI runtimes.

Development runtime/model downloads remain under ignored `.test-runtime`. Ollama initialized standard signing keys under `~/.ollama`; those shared files were not removed. No messaging account was connected and no external messages were sent.
