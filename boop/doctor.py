"""`boop --providers` and `boop --doctor`: what's configured, and does it actually work from here."""
from __future__ import annotations

import os
import socket
import ssl
import time
from urllib.parse import urlsplit

from . import config as cfg
from .net import NetError, explain, proxy_for, ssl_context, trust_source


def _key_status(p: cfg.Provider) -> str:
    if p.type in ("llamacpp", "fake"):
        return "no key needed"
    if p.api_key:
        return f"key from {p.api_key_source} (…{p.api_key[-4:]})" if len(p.api_key) > 8 else f"key from {p.api_key_source}"
    return f"no key ({p.api_key_source or 'not configured'})" if cfg._is_remote(p) else "no key (local)"


def providers(use: str | None = None) -> int:
    raw = cfg.load_file()
    try:
        current = cfg.active(use, raw).name
    except cfg.ConfigError as e:
        current = None
        print(f"config problem: {e}\n")
    names = list((raw.get("providers") or {})) + [n for n in cfg.PRESETS if n not in (raw.get("providers") or {})]
    print(f"config: {cfg.config_path()}" + ("" if cfg.config_path().exists() else "  (none yet: boop --init-config)"))
    print()
    for n in names:
        try:
            p = cfg.provider(n, raw)
        except cfg.ConfigError as e:
            print(f"  ✗ {n:<14} {e}")
            continue
        mark = "●" if n == current else " "
        where = p.base_url or ("local GGUF" if p.type == "llamacpp" else "api.anthropic.com" if p.type == "anthropic" else "")
        if p.type == "llamacpp":
            from .model import DEFAULT_MODEL
            model = (p.model or os.environ.get("BOOP_MODEL") or DEFAULT_MODEL).split("/")[-1]
        else:
            model = p.model or p.deployment or "(set model)"
        kind = "configured" if n in (raw.get("providers") or {}) else "preset"
        print(f"  {mark} {n:<14} {p.type:<10} {model:<28} {where}")
        print(f"    {'':<14} {kind} · {_key_status(p)}")
    print("\n● = in use. Switch with `use = \"NAME\"` in the config, BOOP_PROVIDER=NAME, or boop --use NAME …")
    return 0


def doctor(use: str | None = None) -> int:
    ok = True
    print(f"config    {cfg.config_path()}" + ("" if cfg.config_path().exists() else "  (not created; using defaults)"))
    try:
        p = cfg.active(use)
    except cfg.ConfigError as e:
        print(f"✗ config  {e}")
        return 1
    print(f"provider  {p.name} ({p.type})  model: {p.model or p.deployment or '-'}")
    if p.type == "fake":
        print("✓ fake backend: nothing to check")
        return 0
    url = p.base_url or ("https://api.anthropic.com" if p.type == "anthropic" else "")
    if url:
        print(f"endpoint  {url}")
    print(f"key       {_key_status(p)}")
    net = p.network
    if url.startswith("https"):
        print(f"trust     {trust_source(net)}")
    proxy = proxy_for(url, net) if url else None
    print(f"proxy     {proxy or 'none (direct connection)'}")
    for problem in cfg.check(p):
        print(f"✗ {problem}")
        ok = False
    if not ok:
        return 1

    if url.startswith("https") and not proxy:                 # handshake on its own, so TLS problems show clearly
        parts = urlsplit(url)
        host, port = parts.hostname, parts.port or 443
        try:
            ctx = ssl_context(net)
            with socket.create_connection((host, port), timeout=min(net.timeout, 15)) as sock:
                with ctx.wrap_socket(sock, server_hostname=host) as tls:
                    cert = tls.getpeercert() or {}
                    issuer = dict(x[0] for x in cert.get("issuer", [])).get("organizationName", "?")
                    print(f"✓ TLS     {tls.version()} to {host}, certificate issued by {issuer}, "
                          f"valid until {cert.get('notAfter', '?')}")
        except (OSError, ssl.SSLError, NetError) as e:
            msg = str(e) if isinstance(e, NetError) else explain(e, url, net)
            print(f"✗ TLS     {msg.replace(' Run `boop --doctor` for details.', '')}")
            return 1

    from .context import Context
    from .model import make_backend
    from .prompt import build_messages, parse_answer
    try:
        t0 = time.time()
        be = make_backend(use)
        ctx = Context(os="Linux", shell="bash", cwd=os.getcwd(), files=["notes.txt", "src/"])
        ans = parse_answer(be.generate(build_messages("count the lines in notes.txt", ctx)))
        took = time.time() - t0
    except NetError as e:
        print(f"✗ request {e}")
        return 1
    except SystemExit as e:
        print(f"✗ {e}")
        return 1
    if not ans.command:
        print(f"✗ answer  the model replied but gave no command ({ans.explanation or 'empty'})")
        return 1
    print(f"✓ answer  {ans.command}   ({took:.1f}s)")
    return 0
