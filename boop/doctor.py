"""`boop --providers` and `boop --doctor`: what's configured, and does it actually work from here."""
from __future__ import annotations

import os
import socket
import ssl
import time
from urllib.parse import urlsplit

from . import config as cfg
from .net import NetError, explain, proxy_for, ssl_context, trust_source

_HEX = {"ok": "a6e3a1", "bad": "f38ba8", "dim": "7f849c", "label": "89b4fa", "accent": "cba6f7", "fix": "fab387",
        "url": "94e2d5"}
_BASIC = {"ok": "32", "bad": "31", "dim": "2", "label": "34", "accent": "35", "fix": "33", "url": "36"}


def c(text: str, kind: str, bold: bool = False) -> str:
    """Colour for a terminal (true colour when it says so), plain text for pipes, files and NO_COLOR."""
    import sys
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty() or os.environ.get("TERM") == "dumb":
        return text
    if os.environ.get("COLORTERM") in ("truecolor", "24bit"):
        h = _HEX[kind]
        code = f"38;2;{int(h[:2], 16)};{int(h[2:4], 16)};{int(h[4:], 16)}"
    else:
        code = _BASIC[kind]
    return f"\033[{'1;' if bold else ''}{code}m{text}\033[0m"


def info(label: str, value: str) -> None:
    print(f"  {c(f'{label:<9}', 'label')} {value}")


def good(label: str, value: str) -> None:
    print(f"{c('✓', 'ok', True)} {c(f'{label:<9}', 'ok')} {value}")


def bad(label: str, value: str) -> None:
    """The problem in red; the advice after it on its own line, so it's easy to spot."""
    head, _, fix = value.partition(". ")
    print(f"{c('✗', 'bad', True)} {c(f'{label:<9}', 'bad')} {c(head + ('.' if fix else ''), 'bad')}")
    if fix:
        print(f"  {'':<9} {c('→ ' + fix, 'fix')}")


def _key_status(p: cfg.Provider) -> str:
    if p.type in ("llamacpp", "fake"):
        return "no key needed"
    if p.api_key:
        return f"key from {p.api_key_source} (…{p.api_key[-4:]})" if len(p.api_key) > 8 else f"key from {p.api_key_source}"
    return f"no key ({p.api_key_source or 'not configured'})" if cfg._is_remote(p) else "no key (local)"


def _row(p: cfg.Provider, current: str | None, configured: bool) -> list[str]:
    mark = "●" if p.name == current else " "
    where = p.base_url or ("local GGUF" if p.type == "llamacpp" else "api.anthropic.com" if p.type == "anthropic" else "")
    if p.type == "llamacpp":
        from .model import DEFAULT_MODEL
        model = (p.model or os.environ.get("BOOP_MODEL") or DEFAULT_MODEL).split("/")[-1]
    else:
        model = p.model or p.deployment or "(set model)"
    mark = c("●", "accent", True) if p.name == current else " "
    return [f"  {mark} {c(f'{p.name:<12}', 'label', True)} {c(f'{p.type:<10}', 'dim')} {model:<26} {c(where, 'url')}",
            f"    {'':<12} " + c(f"{'configured' if configured else 'preset'} · {_key_status(p)}", "dim")]


def providers(use: str | None = None) -> int:
    """Your providers in detail, then the remaining presets on one line."""
    raw = cfg.load_file()
    try:
        current = cfg.active(use, raw).name
    except cfg.ConfigError as e:
        current = None
        print(f"config problem: {e}\n")
    mine = list(raw.get("providers") or {})
    print(c("config ", "dim") + str(cfg.config_path()) +
          ("" if cfg.config_path().exists() else c("  (none yet: boop --init-config)", "dim")))
    print()
    shown = mine + ([current] if current and current not in mine and current in cfg.PRESETS else [])
    for n in shown or ["local"]:
        try:
            print("\n".join(_row(cfg.provider(n, raw), current, n in mine)))
        except cfg.ConfigError as e:
            print(f"  {c('✗', 'bad')} {n:<12} {c(str(e), 'bad')}")
    rest = [n for n in cfg.PRESETS if n not in shown]
    print(f"\n  {c('presets', 'dim')}  {', '.join(rest)}")
    print(c("\n  ● in use · switch with use = \"NAME\" in the config, BOOP_PROVIDER=NAME, or boop --use NAME …", "dim"))
    return 0


def doctor(use: str | None = None) -> int:
    ok = True
    info("config", str(cfg.config_path()) + ("" if cfg.config_path().exists() else c("  (not created; using defaults)", "dim")))
    try:
        p = cfg.active(use)
    except cfg.ConfigError as e:
        bad("config", str(e))
        return 1
    info("provider", f"{c(p.name, 'accent', True)} {c(f'({p.type})', 'dim')}  model {p.model or p.deployment or '-'}")
    if p.type == "fake":
        good("fake", "nothing to check")
        return 0
    url = p.base_url or ("https://api.anthropic.com" if p.type == "anthropic" else "")
    if url:
        info("endpoint", c(url, "url"))
    info("key", _key_status(p))
    net = p.network
    if url.startswith("https"):
        info("trust", trust_source(net))
    proxy = proxy_for(url, net) if url else None
    info("proxy", proxy or "none (direct connection)")
    for problem in cfg.check(p):
        bad("config", problem)
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
                    good("TLS", f"{tls.version()} to {host}, certificate issued by {c(issuer, 'accent')}, "
                                f"valid until {cert.get('notAfter', '?')}")
        except (OSError, ssl.SSLError, NetError) as e:
            msg = str(e) if isinstance(e, NetError) else explain(e, url, net)
            bad("TLS", msg.replace(" Run `boop --doctor` for details.", ""))
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
        bad("request", str(e))
        return 1
    except SystemExit as e:
        bad("setup", str(e))
        return 1
    if not ans.command:
        bad("answer", f"the model replied but gave no command ({ans.explanation or 'empty'})")
        return 1
    from .highlight import tokens
    colours = {"class:sh.cmd": "label", "class:sh.flag": "fix", "class:sh.path": "url", "class:sh.string": "ok",
               "class:sh.op": "bad"}
    shown = "".join(c(t, colours[k]) if k in colours else t for k, t in tokens(ans.command))
    good("answer", f"{shown}   {c(f'({took:.1f}s)', 'dim')}")
    return 0
