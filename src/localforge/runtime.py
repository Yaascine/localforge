from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

import httpx
import psutil

from .core import ForgeError, Store, Vault, atomic_json, protect_secret, redact, run
from .install import Installer
from .gguf import metadata
from .download import digest
from .system import port_free


class Runtime:
    """Own only child processes launched by this application; never kill another user's services."""

    def __init__(self, store: Store, vault: Vault | None = None, log=lambda _: None):
        self.store, self.vault, self.log = store, vault or Vault(), log
        self.installer = Installer(store, log)
        self.processes: dict[str, subprocess.Popen] = {}
        self.gateway_token = secrets.token_urlsafe(32)
        self.engine_token = secrets.token_urlsafe(32)
        protect_secret(self.gateway_token)
        protect_secret(self.engine_token)
        self.ollama_port = 11435
        self.llama_port = 8081
        self.gateway_port = 18790

    def env(self):
        env = self.installer.env()
        # Do not implicitly grant cloud credentials or inherit someone else's OpenClaw profile.
        for key in list(env):
            if key.startswith(
                (
                    "OPENCLAW_",
                    "ANTHROPIC_",
                    "OPENAI_",
                    "TELEGRAM_",
                    "DISCORD_",
                    "OLLAMA_",
                    "LLAMA_",
                    "LOCALFORGE_",
                )
            ):
                del env[key]
        env.update(
            OPENCLAW_STATE_DIR=str(self.store.root / "openclaw"),
            OPENCLAW_CONFIG_PATH=str(self.store.root / "openclaw" / "openclaw.json"),
            OPENCLAW_GATEWAY_TOKEN=self.gateway_token,
            OLLAMA_HOST=f"127.0.0.1:{self.ollama_port}",
            OLLAMA_NO_CLOUD="1",
            OLLAMA_MODELS=str(self.store.root / "models" / "ollama"),
            OLLAMA_API_KEY="localforge-local",
            LOCALFORGE_LLAMA_KEY=self.engine_token,
        )
        for provider in ("telegram", "discord"):
            if self.store.state.get(f"{provider}_configured"):
                token = self.vault.get(provider)
                if not token:
                    raise ForgeError(f"{provider} credential is missing from the OS keychain.")
                env[f"{provider.upper()}_BOT_TOKEN"] = token
        return env

    def spawn(self, name, args, port):
        if name in self.processes and self.processes[name].poll() is None:
            return
        if not port_free(port):
            raise ForgeError(f"Port {port} is occupied. Stop the conflicting service before retrying.")
        child_env = self.env()
        if name == "llama.cpp":
            child_env["LLAMA_API_KEY"] = self.engine_token
            child_env["LLAMA_ARG_CORS_ORIGINS"] = "localhost"
        proc = subprocess.Popen(
            args,
            env=child_env,
            cwd=self.store.root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        self.processes[name] = proc

        def pump():
            for line in proc.stdout:
                self.log(redact(line.rstrip()))
            proc.stdout.close()

        threading.Thread(target=pump, daemon=True).start()

    def stop(self, name=None):
        for key in list(self.processes):
            if name and name != key:
                continue
            proc = self.processes.pop(key)
            if proc.poll() is None:
                try:
                    parent = psutil.Process(proc.pid)
                    children = parent.children(recursive=True)
                    for p in children:
                        p.terminate()
                    parent.terminate()
                    _, alive = psutil.wait_procs([parent] + children, timeout=8)
                    for p in alive:
                        p.kill()
                    proc.wait(timeout=5)
                except (psutil.NoSuchProcess, subprocess.TimeoutExpired):
                    pass

    def wait_http(self, name, url, timeout=120):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            proc = self.processes.get(name)
            if not proc or proc.poll() is not None:
                raise ForgeError(f"{name} stopped during startup. See Diagnostics for the runtime error.")
            try:
                with httpx.Client(timeout=2, trust_env=False) as c:
                    r = c.get(url)
                    if r.status_code == 200:
                        return r.json()
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.5)
        self.stop(name)
        raise ForgeError(f"{name} did not become healthy within {timeout} seconds.")

    def start_engine(self, engine, model_path: Path | None = None, context=16384):
        binary = self.store.state.get(engine) or shutil.which(
            "ollama" if engine == "ollama" else "llama-server"
        )
        if not binary:
            raise ForgeError(f"Install {engine} using the reviewed installation plan first.")
        if engine == "ollama":
            self.spawn(engine, [binary, "serve"], self.ollama_port)
            self.wait_http(engine, f"http://127.0.0.1:{self.ollama_port}/api/tags")
        else:
            if not model_path or not model_path.is_file():
                raise ForgeError("A verified GGUF file is required to start llama.cpp.")
            self.stop(engine)
            self.spawn(
                engine,
                [
                    binary,
                    "-m",
                    str(model_path),
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(self.llama_port),
                    "-c",
                    str(context),
                    "-t",
                    str(max(1, (os.cpu_count() or 2) // 2)),
                    "-ngl",
                    "0",
                    "--alias",
                    "localforge-model",
                    "--jinja",
                    "--parallel",
                    "1",
                ],
                self.llama_port,
            )
            self.wait_http(engine, f"http://127.0.0.1:{self.llama_port}/health", timeout=300)

    def register(self, path: Path, sha: str, engine="ollama", context=16384):
        if not path.is_file() or digest(path) != sha:
            raise ForgeError("Model integrity check failed before loading. Download the verified file again.")
        info = metadata(path)
        architecture = info.get("general.architecture", "unknown")
        declared_context = info.get(str(architecture) + ".context_length")
        if isinstance(declared_context, int) and declared_context > 0:
            context = min(context, declared_context)
        self.start_engine(engine, path, context)
        model = "localforge-" + sha[:16] if engine == "ollama" else "localforge-model"
        if engine == "ollama":
            modelfile = self.store.root / "runtime" / "Modelfile"
            # JSON quoting prevents newline/path injection into the generated Modelfile.
            modelfile.write_text(
                f"FROM {json.dumps(str(path))}\nPARAMETER num_ctx {context}\n", encoding="utf-8"
            )
            run(
                [
                    self.store.state.get("ollama") or shutil.which("ollama"),
                    "create",
                    model,
                    "-f",
                    str(modelfile),
                ],
                env=self.env(),
                timeout=1800,
                log=self.log,
            )
        runtime_metadata = {}
        if engine == "ollama":
            with httpx.Client(timeout=30, trust_env=False) as client:
                response = client.post(f"http://127.0.0.1:{self.ollama_port}/api/show", json={"model": model})
                response.raise_for_status()
                details = response.json()
                runtime_metadata = {
                    "capabilities": details.get("capabilities", []),
                    "thinking_values": (details.get("thinking") or {}).get("values", []),
                }
        self.store.update(
            candidate={
                "path": str(path),
                "sha256": sha,
                "engine": engine,
                "model": model,
                "context": context,
                "architecture": architecture,
                "declared_context": declared_context,
                **runtime_metadata,
            }
        )
        return self.store.state["candidate"]

    def infer(self, candidate, messages=None, max_tokens=4096):
        engine, model = candidate["engine"], candidate["model"]
        messages = messages or [{"role": "user", "content": "Say hello in one short sentence."}]
        # Thinking models may spend the whole generation budget before producing content.
        # Never present reasoning-only output as a successful final answer.
        with httpx.Client(timeout=httpx.Timeout(1800, connect=10), trust_env=False) as c:
            for budget in (max_tokens, max_tokens * 2):
                if engine == "ollama":
                    values = candidate.get("thinking_values", [])
                    thinking = (
                        {"think": False}
                        if False in values or (not values and "thinking" in candidate.get("capabilities", []))
                        else ({"think": "low"} if "low" in values else {})
                    )
                    r = c.post(
                        f"http://127.0.0.1:{self.ollama_port}/api/chat",
                        json={
                            "model": model,
                            "messages": messages,
                            "stream": False,
                            "options": {"num_ctx": candidate["context"], "num_predict": budget},
                            **thinking,
                        },
                    )
                else:
                    r = c.post(
                        f"http://127.0.0.1:{self.llama_port}/v1/chat/completions",
                        headers={"Authorization": "Bearer " + self.engine_token},
                        json={
                            "model": model,
                            "messages": messages,
                            "max_tokens": budget,
                            "stream": False,
                            "chat_template_kwargs": {"enable_thinking": False},
                        },
                    )
                r.raise_for_status()
                data = r.json()
                if data.get("error"):
                    raise ForgeError(str(data["error"]))
                choice = (data.get("choices") or [{}])[0]
                message = (data.get("message") if engine == "ollama" else choice.get("message")) or {}
                reply = message.get("content")
                if isinstance(reply, str) and reply.strip():
                    return reply
                reasoning = (
                    message.get("thinking") or message.get("reasoning_content") or message.get("reasoning")
                )
                reason = data.get("done_reason") if engine == "ollama" else choice.get("finish_reason")
                if budget == max_tokens and (reasoning or reason == "length"):
                    self.log("Model returned no final answer; retrying with a larger output budget.")
                    continue
                raise ForgeError(
                    "No final text response was returned"
                    + (
                        " after reasoning exhausted the output budget"
                        if reasoning or reason == "length"
                        else ""
                    )
                    + ". The model is still installed. Use Chat to try another prompt or raise the output-token limit."
                )

    def chat(self, messages, max_tokens=4096):
        candidate = self.store.state.get("candidate") or self.store.state.get("active_model")
        if not candidate:
            downloaded = self.store.state.get("downloaded")
            if not downloaded:
                raise ForgeError("Download a model first.")
            candidate = self.register(
                Path(downloaded["path"]), downloaded["sha256"], downloaded["engine"], downloaded["context"]
            )
        process = self.processes.get(candidate["engine"])
        if not process or process.poll() is not None:
            if digest(Path(candidate["path"])) != candidate["sha256"]:
                raise ForgeError("Model integrity verification failed. Download it again.")
            self.start_engine(candidate["engine"], Path(candidate["path"]), candidate["context"])
        return self.infer(candidate, messages, max_tokens)

    def config(self, candidate=None):
        config = {
            "gateway": {
                "mode": "local",
                "bind": "loopback",
                "port": self.gateway_port,
                "auth": {"mode": "token", "token": "${OPENCLAW_GATEWAY_TOKEN}"},
            },
            "agents": {"defaults": {"workspace": str(self.store.root / "workspace"), "skipBootstrap": True}},
            "tools": {
                "allow": ["read", "session_status"],
                "deny": ["exec", "process", "write", "edit", "browser", "web_fetch", "web_search"],
                "fs": {"workspaceOnly": True},
                "elevated": {"enabled": False},
            },
            "logging": {
                "level": "info",
                "file": str(self.store.root / "logs" / "openclaw.log"),
                "maxFileBytes": 5 * 1024 * 1024,
            },
            "discovery": {"mdns": {"mode": "off"}},
        }
        if candidate:
            engine = candidate["engine"]
            provider = "ollama" if engine == "ollama" else "localforge"
            url = (
                f"http://127.0.0.1:{self.ollama_port}"
                if engine == "ollama"
                else f"http://127.0.0.1:{self.llama_port}/v1"
            )
            config["models"] = {
                "mode": "replace",
                "providers": {
                    provider: {
                        "baseUrl": url,
                        "api": "ollama" if engine == "ollama" else "openai-completions",
                        "apiKey": "localforge-local" if engine == "ollama" else "${LOCALFORGE_LLAMA_KEY}",
                        "models": [
                            {
                                "id": candidate["model"],
                                "name": candidate["model"],
                                "input": ["text"],
                                "compat": {"supportsTools": candidate.get("tools_enabled") is not False},
                                "contextWindow": candidate["context"],
                                "maxTokens": min(2048, candidate["context"] // 4),
                                "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                            }
                        ],
                    }
                },
            }
            config["agents"]["defaults"]["model"] = {"primary": provider + "/" + candidate["model"]}
        if candidate and candidate.get("tools_enabled") is False:
            config["tools"].pop("allow", None)
            config["tools"]["deny"] = ["*"]
        existing = self.store.root / "openclaw" / "openclaw.json"
        if existing.exists():
            previous = json.loads(existing.read_text())
            for key in ("channels", "bindings"):
                if key in previous:
                    config[key] = previous[key]
        return config

    def write_config(self, config):
        path = self.store.root / "openclaw" / "openclaw.json"
        old = path.read_bytes() if path.exists() else None
        if old:
            backup = path.with_name(f"openclaw-{time.time_ns()}.backup.json")
            backup.write_bytes(old)
            backup.chmod(0o600)
        atomic_json(path, config)
        try:
            run(self.installer.claw() + ["config", "validate"], env=self.env(), timeout=90, log=self.log)
        except Exception:
            if old is not None:
                path.write_bytes(old)
                path.chmod(0o600)
            else:
                path.unlink(missing_ok=True)
            raise

    def stage(self):
        active = self.store.state.get("active_model")
        self.write_config(self.config(active))
        self.store.update(
            verification={},
            agent_status="Reverify the existing model" if active else "Staged; waiting for a model",
        )
        return "OpenClaw configuration validated. Model verification is required before readiness."

    def start_gateway(self):
        self.spawn(
            "openclaw",
            self.installer.claw() + ["gateway", "run", "--port", str(self.gateway_port)],
            self.gateway_port,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if self.processes["openclaw"].poll() is not None:
                raise ForgeError("OpenClaw exited during startup. Inspect the runtime log.")
            try:
                run(self.installer.claw() + ["health", "--json"], env=self.env(), timeout=10)
                return
            except ForgeError:
                time.sleep(1)
        self.stop("openclaw")
        raise ForgeError("OpenClaw gateway health check timed out.")

    def agent_turn(self, message, session):
        raw = run(
            self.installer.claw()
            + [
                "agent",
                "--session-id",
                session,
                "--message",
                message,
                "--thinking",
                "off",
                "--json",
                "--timeout",
                "180",
            ],
            env=self.env(),
            timeout=210,
            log=self.log,
        )
        data = json.loads(raw)
        if data.get("ok") is False or data.get("status") in ("error", "failed"):
            raise ForgeError("OpenClaw reported an unsuccessful agent turn.")
        payloads = data.get("result", data).get("payloads", [])
        text = "\n".join(p.get("text", "") for p in payloads)
        if not text.strip():
            raise ForgeError("OpenClaw returned no agent text.")
        return text

    def read_tool_verified(self, session, nonce):
        # Current OpenClaw stores transcripts in SQLite; export through its public CLI.
        export_name = "verification-" + secrets.token_hex(8)
        directory = self.store.root / "workspace" / ".openclaw" / "trajectory-exports" / export_name
        try:
            run(
                self.installer.claw()
                + [
                    "sessions",
                    "export-trajectory",
                    "--session-key",
                    "agent:main:explicit:" + session,
                    "--workspace",
                    str(self.store.root / "workspace"),
                    "--output",
                    export_name,
                    "--json",
                ],
                env=self.env(),
                timeout=60,
            )
            events = directory / "events.jsonl"
            if not events.is_file() or events.stat().st_size > 16 * 1024 * 1024:
                return False
            for line in events.read_text().splitlines():
                event = json.loads(line)
                message = event.get("data", {}).get("message", {})
                if (
                    event.get("sessionId") == session
                    and event.get("type") == "tool.result"
                    and message.get("role") == "toolResult"
                    and message.get("toolName") == "read"
                    and not message.get("isError", False)
                    and nonce in json.dumps(message.get("content", []))
                ):
                    return True
        except (ForgeError, OSError, ValueError):
            return False
        finally:
            if directory.exists() and not directory.is_symlink():
                shutil.rmtree(directory)
        return False

    def verify_agent(self, candidate):
        self.stop("openclaw")
        self.write_config(self.config(candidate))
        self.start_gateway()
        nonce = secrets.token_hex(16)
        fixture = self.store.root / "workspace" / "verification.txt"
        fixture.write_text(nonce)
        session = "localforge-check-" + secrets.token_hex(8)
        tool_ok, detail = False, "A completed read tool action could not be verified."
        try:
            try:
                text = self.agent_turn(
                    f"Call the read tool with path {fixture}. Return only the exact text in that file. Do not return a link or guess the contents.",
                    session,
                )
                tool_ok = nonce in text and self.read_tool_verified(session, nonce)
                if tool_ok:
                    return {
                        "gateway": True,
                        "agent": True,
                        "tools": True,
                        "detail": "Workspace nonce returned and read tool result recorded.",
                    }
            except Exception as exc:
                detail = redact(str(exc))
            # Keep text-only models useful, without claiming agent tools work.
            candidate["tools_enabled"] = False
            self.stop("openclaw")
            self.write_config(self.config(candidate))
            self.start_gateway()
            reply = self.agent_turn(
                "Say hello in one short sentence.", "localforge-text-" + secrets.token_hex(8)
            )
            return {
                "gateway": True,
                "agent": True,
                "tools": False,
                "agent_reply": reply,
                "detail": "Tool verification failed; all tools are disabled for this model. " + detail,
            }
        finally:
            fixture.unlink(missing_ok=True)

    def activate(self, path, sha, engine, context):
        self.stop()
        previous = self.store.state.get("active_model")
        self.store.update(verification={"inference": False, "agent": False, "tools": False})
        candidate = self.register(path, sha, engine, context)
        report = {"inference": False, "agent": False, "tools": False}
        try:
            reply = self.infer(candidate)
            report.update(inference=True, inference_reply=reply)
            if candidate["context"] < 16384:
                candidate["tools_enabled"] = False
                self.write_config(self.config(candidate))
                raise ForgeError(
                    "Inference works, but this model declares a context below OpenClaw's 16K minimum. Choose a model with a larger native context for agent setup."
                )
            report.update(self.verify_agent(candidate))
        except Exception as exc:
            report["detail"] = redact(str(exc))
        self.store.update(
            active_model=candidate,
            previous_model=previous if previous != candidate else self.store.state.get("previous_model"),
            verification=report,
            agent_status="Ready" if report["tools"] else "Limited: agent/tool verification incomplete",
        )
        return report

    def restore_previous(self):
        previous = self.store.state.get("previous_model")
        if not previous:
            raise ForgeError("No previous model is available to restore.")
        self.stop()
        return self.activate(
            Path(previous["path"]), previous["sha256"], previous["engine"], previous["context"]
        )

    def start(self):
        model = self.store.state.get("candidate") or self.store.state.get("active_model")
        if not model:
            raise ForgeError("Download a model first, then send a message from Chat.")
        if digest(Path(model["path"])) != model["sha256"]:
            raise ForgeError(
                "The saved model failed integrity verification. Download it again before starting."
            )
        self.start_engine(model["engine"], Path(model["path"]), model["context"])
        if model["context"] < 16384 or not self.store.state.get("verification", {}).get("gateway"):
            return self.status()
        self.start_gateway()
        return self.status()

    def status(self):
        result = {
            "processes": {
                name: ("Running" if p.poll() is None else f"Exited ({p.returncode})")
                for name, p in self.processes.items()
            },
            "verification": self.store.state.get("verification", {}),
            "model": self.store.state.get("candidate") or self.store.state.get("active_model"),
            "memory": {},
            "engine_models": [],
        }
        for name, p in self.processes.items():
            try:
                process = psutil.Process(p.pid)
                result["memory"][name] = process.memory_info().rss + sum(
                    x.memory_info().rss for x in process.children(recursive=True)
                )
            except psutil.Error:
                pass
        if self.processes.get("ollama") and self.processes["ollama"].poll() is None:
            try:
                with httpx.Client(timeout=2, trust_env=False) as c:
                    r = c.get(f"http://127.0.0.1:{self.ollama_port}/api/ps")
                    r.raise_for_status()
                    result["engine_models"] = r.json().get("models", [])
            except httpx.HTTPError:
                result["engine_health"] = "Unreachable"
        return result

    def configure_messaging(self, provider, token, users):
        protect_secret(token)
        if provider not in ("telegram", "discord"):
            raise ForgeError("Only Telegram and Discord are supported in this release.")
        users = [u.strip() for u in users.split(",") if u.strip()]
        if not users or any(not u.isdigit() or len(u) > 24 for u in users):
            raise ForgeError(
                "Provide a comma-separated allowlist of numeric user IDs. Wildcards are prohibited."
            )
        with httpx.Client(timeout=20, trust_env=False) as c:
            if provider == "telegram":
                r = c.get(f"https://api.telegram.org/bot{token}/getMe")
                if r.status_code != 200 or not r.json().get("ok"):
                    raise ForgeError("Telegram rejected the bot credential.")
            else:
                r = c.get("https://discord.com/api/v10/users/@me", headers={"Authorization": f"Bot {token}"})
                if r.status_code != 200 or not r.json().get("bot"):
                    raise ForgeError("Discord rejected the bot credential.")
        previous_token = self.vault.get(provider)
        previously_configured = self.store.state.get(f"{provider}_configured", False)
        self.vault.set(provider, token)
        self.store.update(**{f"{provider}_configured": True})
        config = self.config(self.store.state.get("active_model"))
        if provider == "telegram":
            settings = {
                "enabled": True,
                "botToken": "${TELEGRAM_BOT_TOKEN}",
                "dmPolicy": "allowlist",
                "allowFrom": users,
                "groupPolicy": "disabled",
                "configWrites": False,
            }
        else:
            settings = {
                "enabled": True,
                "token": "${DISCORD_BOT_TOKEN}",
                "dmPolicy": "allowlist",
                "allowFrom": users,
                "groupPolicy": "disabled",
                "configWrites": False,
            }
        config.setdefault("channels", {})[provider] = settings
        bindings = config.setdefault("bindings", [])
        binding = {"agentId": "main", "match": {"channel": provider, "accountId": "default"}}
        if binding not in bindings:
            bindings.append(binding)
        try:
            self.write_config(config)
        except Exception:
            self.store.update(**{f"{provider}_configured": previously_configured})
            if previous_token:
                self.vault.set(provider, previous_token)
            else:
                self.vault.delete(provider)
            raise
        return "Credential authenticated and allowlist saved. Restart the gateway, then run channel diagnostics to verify connectivity."

    def channel_status(self):
        return run(
            self.installer.claw() + ["channels", "status", "--probe", "--json"],
            env=self.env(),
            timeout=90,
            log=self.log,
        )

    def uninstall(self, delete_models=False):
        self.stop()
        if not (self.store.root / ".localforge-owned").is_file():
            raise ForgeError("Application ownership marker missing; refusing removal.")
        for name in ("runtime", "downloads") + (("models",) if delete_models else ()):
            path = self.store.root / name
            if path.is_symlink():
                raise ForgeError("Refusing to remove a symlinked application directory.")
            shutil.rmtree(path)
            path.mkdir(mode=0o700)
        for key in (
            "node",
            "openclaw",
            "ollama",
            "llama.cpp",
            "active_model",
            "candidate",
            "plan",
            "verification",
        ):
            self.store.state.pop(key, None)
        self.store.update(install_stage="Uninstalled; workspace, configuration and logs preserved")
        return "App-owned runtimes removed. Existing system software and your workspace/configuration are preserved."
