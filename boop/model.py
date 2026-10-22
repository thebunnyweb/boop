"""Where the command comes from. Default: a small local model via llama.cpp (no account, no network after
the one-time download). Settings are environment variables so every shell can set them the same way:

  BOOP_BACKEND     llamacpp (default) | openai | fake
  BOOP_MODEL       Hugging Face repo id or a local .gguf path   (llamacpp)
  BOOP_MODEL_FILE  file inside the repo                         (llamacpp)
  BOOP_API_BASE    e.g. http://localhost:11434/v1 for Ollama    (openai-compatible)
  BOOP_API_MODEL   model name for that server                   (openai-compatible)
  BOOP_API_KEY     only if the server needs one                 (openai-compatible)
"""
from __future__ import annotations

import contextlib
import json
import os
import sys
import urllib.request

from .prompt import OUTPUT_SCHEMA

DEFAULT_MODEL = "Qwen/Qwen2.5-Coder-1.5B-Instruct-GGUF"
DEFAULT_FILE = "qwen2.5-coder-1.5b-instruct-q8_0.gguf"
MAX_TOKENS = 256


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
    def __init__(self, model: str | None = None, filename: str | None = None):
        from llama_cpp import Llama

        model = model or os.environ.get("BOOP_MODEL") or DEFAULT_MODEL
        filename = filename or os.environ.get("BOOP_MODEL_FILE") or DEFAULT_FILE
        kw = dict(n_ctx=4096, n_gpu_layers=-1, verbose=False)   # -1: use the GPU (Metal/CUDA) when built with it
        if model.endswith(".gguf"):
            with _quiet_stderr():
                self.llm = Llama(model_path=model, **kw)
        else:
            from huggingface_hub import try_to_load_from_cache
            if not isinstance(try_to_load_from_cache(model, filename), str):
                print(f"boop: downloading the model once ({model}, ~1.9 GB)…", file=sys.stderr)
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
    """Any /v1/chat/completions server: Ollama, LM Studio, llama-server, vLLM, a hosted API."""

    def __init__(self):
        self.base = os.environ["BOOP_API_BASE"].rstrip("/")
        self.model = os.environ.get("BOOP_API_MODEL", "qwen2.5-coder:1.5b")
        self.key = os.environ.get("BOOP_API_KEY", "")

    def generate(self, messages: list[dict]) -> str:
        body = json.dumps({"model": self.model, "messages": messages, "temperature": 0,
                           "max_tokens": MAX_TOKENS, "response_format": {"type": "json_object"}}).encode()
        req = urllib.request.Request(f"{self.base}/chat/completions", data=body,
                                     headers={"Content-Type": "application/json",
                                              **({"Authorization": f"Bearer {self.key}"} if self.key else {})})
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)["choices"][0]["message"]["content"] or ""


class Fake(Backend):
    """For tests and plumbing checks: BOOP_FAKE='{"pdf": "find . -name *.pdf"}' (substring → command;
    `{cwd}` in a command becomes the current folder)."""

    def __init__(self, table: dict | None = None):
        self.table = table if table is not None else json.loads(os.environ.get("BOOP_FAKE", "{}"))

    def generate(self, messages: list[dict]) -> str:
        content = messages[-1]["content"]
        request = content.rsplit("Request:", 1)[-1].lower()
        cwd = next((l[len("Folder: "):] for l in content.splitlines() if l.startswith("Folder: ")), ".")
        for key, cmd in self.table.items():
            if key.lower() in request:                     # `{cwd}` stands for the current folder
                return json.dumps({"command": cmd.replace("{cwd}", cwd), "explanation": f"fake answer for '{key}'"})
        return json.dumps({"command": "", "explanation": "fake backend has no answer"})


def needs_download() -> bool:
    """True when the default llama.cpp backend would download its model first (it prints progress)."""
    name = os.environ.get("BOOP_BACKEND") or ("openai" if os.environ.get("BOOP_API_BASE") else "llamacpp")
    model = os.environ.get("BOOP_MODEL") or DEFAULT_MODEL
    if name != "llamacpp" or model.endswith(".gguf"):
        return False
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    return not isinstance(try_to_load_from_cache(model, os.environ.get("BOOP_MODEL_FILE") or DEFAULT_FILE), str)


def model_label() -> str:
    """A short name for the welcome box, without loading anything."""
    name = os.environ.get("BOOP_BACKEND") or ("openai" if os.environ.get("BOOP_API_BASE") else "llamacpp")
    if name == "fake":
        return "fake (testing)"
    if name == "openai":
        host = os.environ.get("BOOP_API_BASE", "").split("//")[-1].split("/")[0]
        return f"{os.environ.get('BOOP_API_MODEL', 'qwen2.5-coder:1.5b')} via {host}"
    model = os.environ.get("BOOP_MODEL") or DEFAULT_MODEL
    label = os.path.basename(model) if model.endswith(".gguf") else model.split("/")[-1]
    return label.removesuffix(".gguf").removesuffix("-GGUF").removesuffix("-gguf") + " (local)"


def make_backend(name: str | None = None) -> Backend:
    name = (name or os.environ.get("BOOP_BACKEND") or ("openai" if os.environ.get("BOOP_API_BASE") else "llamacpp"))
    if name == "fake":
        return Fake()
    if name == "openai":
        return OpenAICompatible()
    if name == "llamacpp":
        return LlamaCpp()
    raise SystemExit(f"boop: unknown BOOP_BACKEND '{name}' (use llamacpp, openai or fake)")
