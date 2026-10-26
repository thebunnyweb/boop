"""HTTPS done properly: which certificates to trust, which proxy to use, and errors a person can act on.

Trust, in order:
  1. network.ca_bundle / $BOOP_CA_BUNDLE / $SSL_CERT_FILE / $REQUESTS_CA_BUNDLE: those CAs *plus* the public ones
     (so a corporate root added for an inspecting proxy doesn't break everything else)
  2. network.system_certs (default on) with `truststore`: the OS store, i.e. whatever IT installed in the
     macOS Keychain or the Windows certificate store
  3. certifi's public CA list, then Python's defaults (python.org builds on macOS ship with no CAs at all)
network.verify = false switches checking off; boop prints a warning on every request when it is.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .config import Network


class NetError(Exception):
    """A request failed; the message says what to do about it."""


def ca_file(net: Network) -> str | None:
    for c in (net.ca_bundle, os.environ.get("SSL_CERT_FILE"), os.environ.get("REQUESTS_CA_BUNDLE")):
        if c:
            return os.path.expanduser(c)
    return None


def _certifi() -> str | None:
    try:
        import certifi
        return certifi.where()
    except ImportError:
        return None


def trust_source(net: Network) -> str:
    if not net.verify:
        return "none (verify = false)"
    if ca_file(net):
        return f"{ca_file(net)} + public CAs"
    if net.system_certs and _truststore() is not None:
        return "the OS certificate store (truststore)"
    return "certifi's public CAs" if _certifi() else "Python's default CA paths"


def _truststore():
    if sys.version_info < (3, 10):
        return None
    try:
        import truststore
        return truststore
    except ImportError:
        return None


def ssl_context(net: Network) -> ssl.SSLContext:
    if not net.verify:
        print("boop: warning: certificate checks are OFF (network.verify = false). Anyone on the network path "
              "can read your requests and keys.", file=sys.stderr)
        return ssl._create_unverified_context()
    extra = ca_file(net)
    if extra:
        if not os.path.exists(extra):
            raise NetError(f"CA bundle {extra} does not exist (network.ca_bundle / SSL_CERT_FILE)")
        ctx = ssl.create_default_context(cafile=extra)
        public = _certifi()
        if public:
            ctx.load_verify_locations(cafile=public)
        else:
            ctx.load_default_certs()
        return ctx
    ts = _truststore() if net.system_certs else None
    if ts is not None:
        return ts.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    return ssl.create_default_context(cafile=_certifi())


def proxy_for(url: str, net: Network) -> str | None:
    """The proxy this URL should go through, or None (local hosts and no_proxy entries never use one)."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    rules = [r.strip().lower() for r in (net.no_proxy or os.environ.get("NO_PROXY") or
                                         os.environ.get("no_proxy") or "").split(",") if r.strip()]
    if host in ("localhost", "127.0.0.1", "::1") or any(
            r == "*" or host == r.lstrip(".") or host.endswith("." + r.lstrip(".")) for r in rules):
        return None
    if net.proxy:
        return net.proxy
    env = urllib.request.getproxies()
    return env.get(parts.scheme) or env.get("all")


def apply_to_libraries(net: Network) -> None:
    """Make other HTTP libraries in this process (model downloads via huggingface_hub, the anthropic SDK)
    trust and route the same way."""
    extra = ca_file(net)
    if extra:
        os.environ.setdefault("SSL_CERT_FILE", extra)
        os.environ.setdefault("REQUESTS_CA_BUNDLE", extra)
    elif net.verify and net.system_certs and _truststore() is not None:
        _truststore().inject_into_ssl()
    if net.proxy:
        for k in ("HTTPS_PROXY", "HTTP_PROXY"):
            os.environ.setdefault(k, net.proxy)
    if net.no_proxy:
        os.environ.setdefault("NO_PROXY", net.no_proxy)


def explain(exc: BaseException, url: str, net: Network) -> str:
    """Turn a low-level failure into a sentence with a fix."""
    host = urlsplit(url).hostname or url
    reason = getattr(exc, "reason", exc)
    text = str(reason)
    if isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in text:
        how = ("Behind a corporate proxy or firewall that inspects HTTPS? Point boop at its root certificate: "
               "network.ca_bundle = \"/path/to/corp-root.pem\" in the config (or BOOP_CA_BUNDLE).")
        if "self-signed" in text or "self signed" in text:
            how = "The server uses a self-signed certificate: add it (or its CA) as network.ca_bundle."
        if "hostname" in text.lower() or "doesn't match" in text:
            how = "The certificate is for a different host name: check base_url."
        return f"TLS certificate check failed for {host} ({getattr(reason, 'verify_message', text)}). {how}" \
               f" Trusting: {trust_source(net)}. Run `boop --doctor` for details."
    if isinstance(reason, ssl.SSLError):
        return f"TLS handshake with {host} failed ({text}). A proxy may be interfering; see `boop --doctor`."
    if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in text:
        return f"{host} did not answer within {net.timeout:.0f}s (network.timeout)."
    if isinstance(reason, ConnectionRefusedError) or "Connection refused" in text:
        return f"nothing is listening at {url.split('/v1')[0]}: is the server (Ollama, LM Studio, …) running?"
    if isinstance(reason, socket.gaierror) or "Name or service not known" in text or "nodename nor servname" in text:
        return f"can't resolve {host}: check base_url and your connection (or the proxy settings)."
    return f"request to {host} failed: {text}"


def post_json(url: str, body: dict, *, headers: dict | None = None, net: Network, retries: int = 1) -> dict:
    """POST JSON, return JSON. Retries once on 429/5xx/connection resets; raises NetError with a fix."""
    data = json.dumps(body).encode()
    hdrs = {"Content-Type": "application/json", "User-Agent": "boop-cli", **(headers or {})}
    proxy = proxy_for(url, net)
    handlers = [urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}),
                urllib.request.HTTPSHandler(context=ssl_context(net))]
    opener = urllib.request.build_opener(*handlers)
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method="POST")
        try:
            with opener.open(req, timeout=net.timeout) as r:
                return json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            detail = _error_detail(e)
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(10.0, float(e.headers.get("retry-after") or 2)))
                continue
            raise NetError(_http_message(e.code, url, detail)) from e
        except (urllib.error.URLError, OSError) as e:
            if attempt < retries and isinstance(getattr(e, "reason", e), (ConnectionResetError,)):
                continue
            raise NetError(explain(e, url, net)) from e
    raise NetError(f"request to {url} failed")            # pragma: no cover


def _error_detail(e: urllib.error.HTTPError) -> str:
    try:
        raw = e.read().decode("utf-8", "replace")
    except Exception:
        return ""
    try:
        j = json.loads(raw)
        err = j.get("error", j)
        return (err.get("message") if isinstance(err, dict) else str(err)) or raw[:300]
    except ValueError:
        return raw[:300]


def _http_message(code: int, url: str, detail: str) -> str:
    host = urlsplit(url).hostname
    hint = {401: "the API key was rejected: check the key for this provider (boop --providers shows its source)",
            403: "the key isn't allowed to use this model or endpoint",
            404: "not found: check base_url and the model name",
            407: "the proxy wants credentials: proxy = \"http://user:password@host:port\"",
            429: "rate limited or out of credit: try again shortly",
            }.get(code, "the server returned an error")
    return f"{host} said HTTP {code}: {hint}." + (f" ({detail})" if detail else "")
