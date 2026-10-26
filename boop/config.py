"""Which model answers: providers, presets and network settings from one config file.

  ~/.config/boop/config.toml      (macOS/Linux; $XDG_CONFIG_HOME respected)
  %APPDATA%\\boop\\config.toml      (Windows)

    use = "work"                       # the provider boop uses; override with --use or BOOP_PROVIDER

    [providers.work]
    preset = "openai"                  # start from a preset, change what you need
    model = "gpt-..."
    api_key_cmd = "op read op://dev/openai/key"     # or api_key_env = "...", or api_key = "..."

    [providers.home]
    preset = "ollama"
    model = "qwen2.5-coder:7b"

    [network]                          # applies to every provider (each can override it)
    ca_bundle = "~/certs/corp-root.pem"   # extra CAs, e.g. a corporate proxy that inspects HTTPS
    system_certs = true                # trust the OS certificate store (Keychain, Windows store)
    proxy = "http://proxy.corp:8080"   # default: HTTPS_PROXY / HTTP_PROXY from the environment
    no_proxy = "localhost,127.0.0.1,.corp"
    timeout = 60

`boop --providers` lists what's configured, `boop --doctor` tests the connection and explains failures,
`boop --init-config` writes a commented starter file.
"""
from __future__ import annotations

import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path

# type: llamacpp (local GGUF) | openai (any OpenAI-compatible /chat/completions) | anthropic | azure | fake
PRESETS: dict[str, dict] = {
    "local": {"type": "llamacpp"},
    "ollama": {"type": "openai", "base_url": "http://localhost:11434/v1", "model": "qwen2.5-coder:7b"},
    "lmstudio": {"type": "openai", "base_url": "http://localhost:1234/v1"},
    "llama-server": {"type": "openai", "base_url": "http://localhost:8080/v1", "model": "local"},
    "openai": {"type": "openai", "base_url": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY"},
    "anthropic": {"type": "anthropic", "model": "claude-opus-5-5", "effort": "low",
                  "api_key_env": "ANTHROPIC_API_KEY"},
    "gemini": {"type": "openai", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
               "api_key_env": "GEMINI_API_KEY"},
    "openrouter": {"type": "openai", "base_url": "https://openrouter.ai/api/v1", "api_key_env": "OPENROUTER_API_KEY"},
    "groq": {"type": "openai", "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"},
    "mistral": {"type": "openai", "base_url": "https://api.mistral.ai/v1", "api_key_env": "MISTRAL_API_KEY"},
    "deepseek": {"type": "openai", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
                 "api_key_env": "DEEPSEEK_API_KEY"},
    "together": {"type": "openai", "base_url": "https://api.together.xyz/v1", "api_key_env": "TOGETHER_API_KEY"},
    "azure": {"type": "azure", "api_version": "2024-10-21", "api_key_env": "AZURE_OPENAI_API_KEY"},
}
NEEDS_KEY = ("openai", "anthropic", "gemini", "openrouter", "groq", "mistral", "deepseek", "together", "azure")


@dataclass
class Network:
    ca_bundle: str | None = None
    system_certs: bool = True
    verify: bool = True                 # false = no certificate checks at all; boop warns every time
    proxy: str | None = None
    no_proxy: str = ""
    timeout: float = 60.0


@dataclass
class Provider:
    name: str
    type: str
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    api_key_source: str = ""            # for --providers / --doctor: where the key comes from, never the key
    headers: dict = field(default_factory=dict)
    effort: str | None = None           # anthropic: output_config.effort
    deployment: str | None = None       # azure
    api_version: str | None = None      # azure
    file: str | None = None             # llamacpp: file inside the HF repo
    network: Network = field(default_factory=Network)


class ConfigError(Exception):
    pass


def config_path() -> Path:
    if os.environ.get("BOOP_CONFIG"):
        return Path(os.environ["BOOP_CONFIG"]).expanduser()
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "boop" / "config.toml"


def load_file(path: Path | None = None) -> dict:
    path = path or config_path()
    if not path.exists():
        return {}
    try:
        import tomllib                                   # 3.11+
    except ImportError:                                  # pragma: no cover - older Pythons
        import tomli as tomllib
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except Exception as e:
        raise ConfigError(f"{path}: {e}") from e


def _network(raw: dict | None, base: Network | None = None) -> Network:
    n = replace(base) if base else Network()
    for k, v in (raw or {}).items():
        if not hasattr(n, k):
            raise ConfigError(f"unknown network setting '{k}' (use ca_bundle, system_certs, verify, proxy, no_proxy, "
                              "timeout)")
        setattr(n, k, v)
    if isinstance(n.ca_bundle, str):
        n.ca_bundle = os.path.expanduser(n.ca_bundle)
    if os.environ.get("BOOP_CA_BUNDLE"):
        n.ca_bundle = os.path.expanduser(os.environ["BOOP_CA_BUNDLE"])
    return n


def _key(name: str, spec: dict) -> tuple[str | None, str]:
    if spec.get("api_key_cmd"):
        cmd = spec["api_key_cmd"]
        try:
            r = subprocess.run(cmd if sys.platform.startswith("win") else shlex.split(cmd),
                               capture_output=True, text=True, timeout=20, shell=sys.platform.startswith("win"))
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ConfigError(f"providers.{name}.api_key_cmd failed: {e}") from e
        if r.returncode != 0 or not r.stdout.strip():
            raise ConfigError(f"providers.{name}.api_key_cmd exited {r.returncode}: {r.stderr.strip()[:200]}")
        return r.stdout.strip(), f"command `{cmd.split()[0]} …`"
    if spec.get("api_key"):
        return str(spec["api_key"]), "config file"
    env = spec.get("api_key_env")
    if env:
        return os.environ.get(env) or None, f"${env}"
    return None, ""


def provider(name: str, raw: dict | None = None) -> Provider:
    """A provider from the config file, a preset name, or both (a configured provider can start from a preset)."""
    raw = load_file() if raw is None else raw
    configured = (raw.get("providers") or {}).get(name)
    if configured is None and name not in PRESETS:
        known = sorted(set(PRESETS) | set(raw.get("providers") or {}))
        raise ConfigError(f"no provider '{name}'. Configured or built in: {', '.join(known)}")
    spec = dict(PRESETS.get((configured or {}).get("preset", name), {}))
    if configured and configured.get("preset") and configured["preset"] not in PRESETS:
        raise ConfigError(f"providers.{name}: unknown preset '{configured['preset']}' (try: {', '.join(PRESETS)})")
    spec.update({k: v for k, v in (configured or {}).items() if k != "preset"})
    if "type" not in spec:
        spec["type"] = "openai" if spec.get("base_url") else "llamacpp"
    net = _network(spec.pop("network", None), _network(raw.get("network")))
    key, source = _key(name, spec)
    p = Provider(name=name, type=spec["type"], model=spec.get("model"), base_url=spec.get("base_url"),
                 api_key=key, api_key_source=source, headers=dict(spec.get("headers") or {}),
                 effort=spec.get("effort"), deployment=spec.get("deployment"), api_version=spec.get("api_version"),
                 file=spec.get("file"), network=net)
    return p


def active(use: str | None = None, raw: dict | None = None) -> Provider:
    """--use > BOOP_PROVIDER > `use` in the config file > the old BOOP_BACKEND / BOOP_API_BASE variables > local."""
    raw = load_file() if raw is None else raw
    legacy = os.environ.get("BOOP_BACKEND")
    if legacy == "fake" and not use:
        return Provider(name="fake", type="fake")
    name = use or os.environ.get("BOOP_PROVIDER") or raw.get("use")
    if name:
        return provider(name, raw)
    if legacy == "openai" or os.environ.get("BOOP_API_BASE"):    # the env-only setup from before the config file
        return Provider(name="env", type="openai", base_url=os.environ.get("BOOP_API_BASE", "").rstrip("/"),
                        model=os.environ.get("BOOP_API_MODEL", "qwen2.5-coder:1.5b"),
                        api_key=os.environ.get("BOOP_API_KEY") or None, api_key_source="$BOOP_API_KEY",
                        network=_network(raw.get("network")))
    if legacy and legacy not in ("llamacpp", "local"):
        raise ConfigError(f"unknown BOOP_BACKEND '{legacy}' (use llamacpp, openai or fake, or set up a provider)")
    return provider("local", raw)


def check(p: Provider) -> list[str]:
    """Problems that would make the first request fail, in plain words."""
    out = []
    if p.type in ("openai", "azure") and not p.base_url:
        out.append(f"providers.{p.name}: set base_url" + (" (https://<resource>.openai.azure.com)" if p.type == "azure" else ""))
    if p.type in ("openai", "anthropic") and not p.model:
        out.append(f"providers.{p.name}: set model = \"…\" (the provider's model name)")
    if p.type == "azure" and not p.deployment:
        out.append(f"providers.{p.name}: set deployment = \"…\" (your Azure deployment name)")
    if p.type in ("openai", "anthropic", "azure") and not p.api_key and _is_remote(p):
        hint = p.api_key_source or "api_key_env / api_key_cmd"
        out.append(f"providers.{p.name}: no API key ({hint} is empty)")
    if p.network.ca_bundle and not os.path.exists(p.network.ca_bundle):
        out.append(f"network.ca_bundle: {p.network.ca_bundle} does not exist")
    return out


def _is_remote(p: Provider) -> bool:
    if p.type == "anthropic":
        return True
    host = (p.base_url or "").split("//")[-1].split("/")[0].split(":")[0]
    return host not in ("localhost", "127.0.0.1", "::1", "0.0.0.0") and bool(host)


STARTER = '''# boop configuration. Pick a provider with `use`, or per run: boop --use NAME …
# Built-in presets: local, ollama, lmstudio, llama-server, openai, anthropic, gemini, openrouter, groq,
# mistral, deepseek, together, azure. A [providers.X] table can start from one with preset = "…".

use = "local"                 # boop's own small model, runs on your machine

# [providers.ollama]
# preset = "ollama"
# model = "qwen2.5-coder:7b"

# [providers.claude]
# preset = "anthropic"         # needs: pipx inject boop-cli anthropic   (or pip install "boop-cli[anthropic]")
# model = "claude-opus-5-5"
# effort = "low"
# api_key_env = "ANTHROPIC_API_KEY"

# [providers.work]
# preset = "openai"            # any OpenAI-compatible API: set base_url for gateways / vLLM / LiteLLM
# model = "…"
# api_key_cmd = "security find-generic-password -s boop-openai -w"   # macOS Keychain; or `op read …`, `pass …`

# [providers.azure]
# preset = "azure"
# base_url = "https://<resource>.openai.azure.com"
# deployment = "<deployment name>"

# [network]
# ca_bundle = "~/certs/corp-root.pem"   # behind a proxy that inspects HTTPS: its root CA (PEM)
# system_certs = true                   # also trust the OS certificate store
# proxy = "http://proxy.corp:8080"      # default: HTTPS_PROXY / HTTP_PROXY
# no_proxy = "localhost,127.0.0.1"
# timeout = 60
'''


def write_starter(path: Path | None = None) -> tuple[Path, bool]:
    path = path or config_path()
    if path.exists():
        return path, False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(STARTER, encoding="utf-8")
    return path, True
