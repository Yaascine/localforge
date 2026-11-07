# Official documentation reviewed — 2026-11-07

- [OpenClaw installation](https://docs.openclaw.ai/install): Node 24.16+ on 24.x or 26.1+, with newer security releases recommended. Private-prefix npm installation is supported. Current npm releases have lifecycle-script approval behavior that differs from older releases.
- [Windows platform](https://docs.openclaw.ai/platforms/windows): native Windows CLI/Gateway is available; Windows Hub is the recommended desktop route and can manage WSL. WSL2 remains the most Linux-compatible Gateway runtime. LocalForge implements native Windows runtime provisioning; it does not create or modify WSL distributions.
- [Ollama provider](https://docs.openclaw.ai/providers/ollama): native Ollama transport is required; appending `/v1` can break tool behavior.
- [Ollama configuration](https://docs.openclaw.ai/providers/ollama/configuration): explicit local provider/model entries are supported.
- [Ollama model import](https://docs.ollama.com/import): GGUF import uses a Modelfile and `ollama create`; import is not quantization.
- [Hugging Face Hub API](https://huggingface.co/docs/hub/en/api): repository metadata and immutable revision resolution are available through supported APIs.
- [OpenClaw tool policy](https://docs.openclaw.ai/gateway/config-tools/tool-policy): allow/deny policy applies independently from profiles and execution permissions.
- [OpenClaw agent CLI](https://docs.openclaw.ai/cli/agent): gateway turns use session selectors and JSON output; command completion alone does not establish a tool result.
- [Telegram](https://docs.openclaw.ai/channels/telegram) and [Discord](https://docs.openclaw.ai/channels/discord): channel authentication and allowlist configuration are provider-specific. No credentials are bundled in this project.
- Official release APIs: [Ollama](https://api.github.com/repos/ollama/ollama/releases/latest), [llama.cpp releases](https://api.github.com/repos/ggml-org/llama.cpp/releases). llama.cpp's semantic latest release may contain only a tag file; the planner also checks recent published binary releases.

Documentation changes independently of published npm releases. Configuration is validated by the actual installed OpenClaw CLI, and startup failures are reported. Backend artifacts can be unavailable for a platform; the planner refuses an unverified download instead of claiming universal compatibility.
