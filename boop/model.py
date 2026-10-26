"""Where the command comes from: the provider picked in the config file (see config.py), by default boop's
own small model running locally via llama.cpp (no account, no network after the one-time download).

Older environment variables still work: BOOP_BACKEND=llamacpp|openai|fake, BOOP_MODEL / BOOP_MODEL_FILE for the
local model, BOOP_API_BASE / BOOP_API_MODEL / BOOP_API_KEY for an OpenAI-compatible server.
"""
from __future__ import annotations

import contextlib
import json
import os
import sys

from . import config as cfg
from .net import NetError, apply_to_libraries, explain, post_json, proxy_for, ssl_context
from .prompt import OUTPUT_SCHEMA

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
DEFAULT_FILE = "qwen2.5-coder-1.5b-instruct-q8_0.gguf"
MAX_TOKENS = 256
REMOTE_MAX_TOKENS = 1024       # hosted reasoning models spend tokens before answering


class Backend:
    def generate(self, messages: list[dict]) -> str:
        raise NotImplementedError


@contextlib.contextmanager
def _quiet_stderr():
    """llama.cpp prints Metal/CUDA init logs straight to fd 2; hide them unless BOOP_DEBUG is set."""
    if os.environ.get("BOOP_DEBUG"):
        yield
        return
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        yield
        return
    saved = os.dup(fd)
    try:
        with open(os.devnull, "w") as null:
            os.dup2(null.fileno(), fd)
            yield
    finally:
        os.dup2(saved, fd)
        os.close(saved)


class LlamaCpp(Backend):
    def __init__(self, model: str | None = None, filename: str | None = None, network: cfg.Network | None = None):
        from llama_cpp import Llama

        apply_to_libraries(network or cfg.Network())     # the one-time download honours ca_bundle / proxy too
        model = model or os.environ.get("BOOP_MODEL") or DEFAULT_MODEL
        filename = filename or os.environ.get("BOOP_MODEL_FILE") or DEFAULT_FILE
        kw = dict(n_ctx=4096, n_gpu_layers=-1, verbose=False)   # -1: use the GPU (Metal/CUDA) when built with it
        if model.endswith(".gguf"):
            with _quiet_stderr():
                self.llm = Llama(model_path=model, **kw)
        else:
            from huggingface_hub import try_to_load_from_cache
            if not isinstance(try_to_load_from_cache(model, filename), str):
                print(f"boop: downloading the model once ({model})…", file=sys.stderr)
                self.llm = Llama.from_pretrained(repo_id=model, filename=filename, **kw)
            else:
                with _quiet_stderr():
                    self.llm = Llama.from_pretrained(repo_id=model, filename=filename, **kw)

    def generate(self, messages: list[dict]) -> str:
        out = self.llm.create_chat_completion(
            messages=messages, temperature=0.0, max_tokens=MAX_TOKENS,
            response_format={"type": "json_object", "schema": OUTPUT_SCHEMA})
        return out["choices"][0]["message"]["content"] or ""


class OpenAICompatible(Backend):
    """Any /chat/completions API: OpenAI, Gemini, Groq, OpenRouter, Mistral, DeepSeek, Together, Azure OpenAI,
    Ollama, LM Studio, llama-server, vLLM, LiteLLM… Providers differ in which knobs they accept, so a 400 that
    names one (response_format, temperature, max_tokens) is retried without it."""

    def __init__(self, p: cfg.Provider):
        self.p = p
        if p.type == "azure":
            base = (p.base_url or "").rstrip("/")
            self.url = f"{base}/openai/deployments/{p.deployment}/chat/completions?api-version={p.api_version}"
            self.headers = {"api-key": p.api_key or "", **p.headers}
        else:
            self.url = f"{(p.base_url or '').rstrip('/')}/chat/completions"
            self.headers = {**({"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}), **p.headers}

    def generate(self, messages: list[dict]) -> str:
        body = {"messages": messages, "temperature": 0, "max_tokens": REMOTE_MAX_TOKENS,
                "response_format": {"type": "json_object"}}
        if self.p.type != "azure":
            body["model"] = self.p.model
        for _ in range(4):
            try:
                out = post_json(self.url, body, headers=self.headers, net=self.p.network)
                break
            except NetError as e:
                msg = str(e)
                if "HTTP 400" not in msg and "HTTP 422" not in msg:
                    raise
                if "response_format" in msg and "response_format" in body:
                    body.pop("response_format")
                elif "max_tokens" in msg and "max_tokens" in body:      # newer OpenAI models
                    body["max_completion_tokens"] = body.pop("max_tokens")
                elif "temperature" in msg and "temperature" in body:    # reasoning models
                    body.pop("temperature")
                else:
                    raise
        choice = (out.get("choices") or [{}])[0]
        return (choice.get("message") or {}).get("content") or ""


class Anthropic(Backend):
    """Claude via the official SDK (pip install anthropic), with boop's trust and proxy settings."""

    def __init__(self, p: cfg.Provider):
        try:
            import anthropic
        except ImportError:
            raise NetError("the anthropic provider needs the Anthropic SDK: pip install \"boop-cli[anthropic]\" "
                           "(or pipx inject boop-cli anthropic)") from None
        self.anthropic, self.p = anthropic, p
        url = p.base_url or "https://api.anthropic.com"
        proxy = proxy_for(url, p.network)
        http = anthropic.DefaultHttpxClient(verify=ssl_context(p.network), proxy=proxy, trust_env=False)
        self.client = anthropic.Anthropic(api_key=p.api_key or None, base_url=p.base_url or None,
                                          http_client=http, timeout=p.network.timeout, max_retries=2,
                                          default_headers=p.headers or None)

    def generate(self, messages: list[dict]) -> str:
        a = self.anthropic
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        schema = {**OUTPUT_SCHEMA, "additionalProperties": False}
        output_config = {"format": {"type": "json_schema", "schema": schema}}
        if self.p.effort:
            output_config["effort"] = self.p.effort
        try:
            r = self.client.messages.create(model=self.p.model, max_tokens=16000, system=system, messages=convo,
                                            output_config=output_config)
        except a.AuthenticationError as e:
            raise NetError(f"Anthropic rejected the API key ({self.p.api_key_source or 'no key set'}).") from e
        except a.PermissionDeniedError as e:
            raise NetError(f"the key can't use {self.p.model}: {e.message}") from e
        except a.NotFoundError as e:
            raise NetError(f"model {self.p.model} not found: check model in the config") from e
        except a.RateLimitError as e:
            raise NetError("Anthropic rate limit hit: try again shortly") from e
        except a.APIStatusError as e:
            raise NetError(f"Anthropic API error {e.status_code}: {e.message}") from e
        except a.APIConnectionError as e:
            cause = e.__cause__ or e
            while getattr(cause, "__cause__", None) and not isinstance(cause, OSError):
                cause = cause.__cause__
            raise NetError(explain(cause, self.p.base_url or "https://api.anthropic.com", self.p.network)) from e
        if r.stop_reason == "refusal":
            return json.dumps({"command": "", "explanation": "the model declined this request"})
        return next((b.text for b in r.content if b.type == "text"), "")


class Fake(Backend):
    """For tests, demos and plumbing checks: BOOP_FAKE='{"pdf": "find . -name *.pdf"}' (substring → command,
    or → [command, explanation]; `{cwd}` in a command becomes the current folder). BOOP_FAKE_DELAY=1.2 makes
    it "think" for that many seconds."""

    def __init__(self, table: dict | None = None):
        self.table = table if table is not None else json.loads(os.environ.get("BOOP_FAKE", "{}"))

    def generate(self, messages: list[dict]) -> str:
        content = messages[-1]["content"]
        request = content.rsplit("Request:", 1)[-1].lower()
        cwd = next((l[len("Folder: "):] for l in content.splitlines() if l.startswith("Folder: ")), ".")
        import time
        time.sleep(float(os.environ.get("BOOP_FAKE_DELAY") or 0))
        for key, val in self.table.items():
            if key.lower() in request:                     # `{cwd}` stands for the current folder
                cmd, why = (val[0], val[1]) if isinstance(val, list) else (val, f"fake answer for '{key}'")
                return json.dumps({"command": cmd.replace("{cwd}", cwd), "explanation": why})
        return json.dumps({"command": "", "explanation": "fake backend has no answer"})


def _active() -> cfg.Provider:
    return cfg.active()


def needs_download() -> bool:
    """True when the local llama.cpp model would download first (it prints progress)."""
    try:
        p = _active()
    except cfg.ConfigError:
        return False
    model = p.model or os.environ.get("BOOP_MODEL") or DEFAULT_MODEL
    if p.type != "llamacpp" or model.endswith(".gguf"):
        return False
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return not isinstance(try_to_load_from_cache(model, p.file or os.environ.get("BOOP_MODEL_FILE") or DEFAULT_FILE), str)


def model_label() -> str:
    """A short name for the panel, without loading anything. BOOP_MODEL_LABEL overrides it."""
    if os.environ.get("BOOP_MODEL_LABEL"):
        return os.environ["BOOP_MODEL_LABEL"]
    try:
        p = _active()
    except cfg.ConfigError:
        return "?"
    if p.type == "fake":
        return "fake (testing)"
    if p.type == "llamacpp":
        model = p.model or os.environ.get("BOOP_MODEL") or DEFAULT_MODEL
        label = os.path.basename(model) if model.endswith(".gguf") else model.split("/")[-1]
        return label.removesuffix(".gguf").removesuffix("-GGUF").removesuffix("-gguf") + " (local)"
    return f"{p.model or p.deployment} · {p.name}"


def make_backend(use: str | None = None) -> Backend:
    try:
        p = cfg.active(use)
    except cfg.ConfigError as e:
        raise SystemExit(f"boop: config: {e}")
    if p.type == "fake":
        return Fake()
    problems = cfg.check(p)
    if problems:
        raise SystemExit("boop: " + "\n      ".join(problems) + f"\n      (config: {cfg.config_path()})")
    if p.type in ("openai", "azure"):
        return OpenAICompatible(p)
    if p.type == "anthropic":
        try:
            return Anthropic(p)
        except NetError as e:
            raise SystemExit(f"boop: {e}")
    if p.type == "llamacpp":
        return LlamaCpp(p.model, p.file, p.network)
    raise SystemExit(f"boop: provider {p.name}: unknown type '{p.type}' (llamacpp, openai, anthropic, azure)")
