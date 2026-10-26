"""Providers end to end against real local servers: plain HTTP, HTTPS with a private CA, and a proxy."""
import http.server
import json
import os
import shutil
import ssl
import subprocess
import sys
import threading

import pytest

from boop import config as cfg
from boop.model import OpenAICompatible
from boop.net import NetError, proxy_for, ssl_context

ANSWER = {"command": "wc -l < notes.txt", "explanation": "Counts lines."}
MSGS = [{"role": "system", "content": "sys"}, {"role": "user", "content": "count lines in notes.txt"}]


class Api(http.server.BaseHTTPRequestHandler):
    """/v1/chat/completions (OpenAI-style) and /v1/messages (Anthropic-style); records what it got."""
    seen: list = []
    reject: set = set()                 # body fields to refuse with a 400, like picky providers do

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Api.seen.append({"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
        bad = Api.reject & set(body)
        if bad:
            return self._send(400, {"error": {"message": f"Unsupported parameter: '{bad.pop()}'"}})
        if self.headers.get("Authorization") == "Bearer wrong":
            return self._send(401, {"error": {"message": "invalid api key"}})
        if self.path.startswith("/v1/messages"):
            return self._send(200, {"id": "msg_1", "type": "message", "role": "assistant", "model": body["model"],
                                    "content": [{"type": "text", "text": json.dumps(ANSWER)}],
                                    "stop_reason": "end_turn", "stop_sequence": None,
                                    "usage": {"input_tokens": 1, "output_tokens": 1}})
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": json.dumps(ANSWER)}}]})

    def _send(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def serve(handler, tls=None):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    if tls:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(*tls)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"{'https' if tls else 'http'}://127.0.0.1:{srv.server_address[1]}"


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    Api.seen, Api.reject = [], set()
    for k in ("BOOP_BACKEND", "BOOP_PROVIDER", "BOOP_API_BASE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
              "BOOP_CA_BUNDLE", "HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "NO_PROXY", "no_proxy"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("BOOP_CONFIG", str(tmp_path / "config.toml"))


@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    """A private CA and a certificate for 127.0.0.1 signed by it, like a corporate proxy's."""
    if not shutil.which("openssl"):
        pytest.skip("needs openssl")
    d = tmp_path_factory.mktemp("certs")
    run = lambda *a: subprocess.run(["openssl", *a], cwd=d, check=True, capture_output=True)
    run("req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-days", "2",
        "-subj", "/CN=Boop Test Root CA/O=Boop Corp", "-addext", "basicConstraints=critical,CA:TRUE",
        "-addext", "keyUsage=critical,keyCertSign,cRLSign")
    run("req", "-newkey", "rsa:2048", "-nodes", "-keyout", "srv.key", "-out", "srv.csr", "-subj", "/CN=127.0.0.1")
    (d / "ext.cnf").write_text("subjectAltName=IP:127.0.0.1,DNS:localhost\n")
    run("x509", "-req", "-in", "srv.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "srv.pem",
        "-days", "2", "-extfile", "ext.cnf")
    return d


def write_config(text):
    cfg.config_path().write_text(text)


# ---- choosing a provider -------------------------------------------------------------------------

def test_presets_config_and_precedence(monkeypatch):
    write_config('use = "work"\n[providers.work]\npreset = "groq"\nmodel = "m1"\napi_key = "k"\n'
                 '[providers.home]\npreset = "ollama"\n')
    p = cfg.active()
    assert (p.name, p.type, p.base_url, p.model) == ("work", "openai", "https://api.groq.com/openai/v1", "m1")
    assert cfg.active().api_key_source == "config file"
    monkeypatch.setenv("BOOP_PROVIDER", "home")
    assert cfg.active().model == "qwen2.5-coder:7b"                  # env beats the file
    assert cfg.active("anthropic").type == "anthropic"              # --use beats both; presets work unconfigured
    monkeypatch.setenv("BOOP_BACKEND", "fake")
    assert cfg.active().type == "fake" and cfg.active("home").name == "home"


def test_keys_from_env_and_command(monkeypatch):
    write_config('[providers.a]\npreset = "openai"\nmodel = "x"\n'
                 f'[providers.b]\npreset = "openai"\nmodel = "x"\napi_key_cmd = "{sys.executable} -c \'print(123456789)\'"\n')
    assert cfg.check(cfg.provider("a")) == ["providers.a: no API key ($OPENAI_API_KEY is empty)"]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert cfg.provider("a").api_key == "sk-test" and cfg.check(cfg.provider("a")) == []
    assert cfg.provider("b").api_key == "123456789"


def test_helpful_config_errors():
    write_config('[providers.x]\npreset = "nope"\n')
    with pytest.raises(cfg.ConfigError, match="unknown preset"):
        cfg.provider("x")
    with pytest.raises(cfg.ConfigError, match="no provider 'zzz'"):
        cfg.provider("zzz")
    assert "set model" in " ".join(cfg.check(cfg.provider("openai")))
    write_config('[network]\nverfy = false\n')
    with pytest.raises(cfg.ConfigError, match="unknown network setting 'verfy'"):
        cfg.provider("ollama")


def test_old_environment_variables_still_work(monkeypatch):
    monkeypatch.setenv("BOOP_API_BASE", "http://localhost:11434/v1/")
    monkeypatch.setenv("BOOP_API_MODEL", "qwen")
    p = cfg.active()
    assert (p.type, p.base_url, p.model) == ("openai", "http://localhost:11434/v1", "qwen")


# ---- talking to it -----------------------------------------------------------------------------------

def test_openai_compatible_over_http():
    srv, url = serve(Api)
    try:
        p = cfg.Provider(name="t", type="openai", base_url=url + "/v1", model="m", api_key="sk-1")
        assert json.loads(OpenAICompatible(p).generate(MSGS)) == ANSWER
        got = Api.seen[-1]
        assert got["path"] == "/v1/chat/completions" and got["headers"]["authorization"] == "Bearer sk-1"
        assert got["body"]["model"] == "m" and got["body"]["response_format"] == {"type": "json_object"}
    finally:
        srv.shutdown()


def test_picky_providers_get_a_retry_without_the_knob():
    srv, url = serve(Api)
    Api.reject = {"response_format", "temperature"}
    try:
        p = cfg.Provider(name="t", type="openai", base_url=url + "/v1", model="m")
        assert json.loads(OpenAICompatible(p).generate(MSGS)) == ANSWER
        assert "response_format" not in Api.seen[-1]["body"] and "temperature" not in Api.seen[-1]["body"]
    finally:
        srv.shutdown()


def test_azure_url_and_header():
    srv, url = serve(Api)
    try:
        p = cfg.Provider(name="az", type="azure", base_url=url, deployment="boop", api_version="2024-10-21",
                         api_key="az-key")
        OpenAICompatible(p).generate(MSGS)
        got = Api.seen[-1]
        assert got["path"] == "/openai/deployments/boop/chat/completions?api-version=2024-10-21"
        assert got["headers"]["api-key"] == "az-key" and "model" not in got["body"]
    finally:
        srv.shutdown()


def test_bad_key_and_dead_server_explain_themselves():
    srv, url = serve(Api)
    try:
        p = cfg.Provider(name="t", type="openai", base_url=url + "/v1", model="m", api_key="wrong")
        with pytest.raises(NetError, match="HTTP 401: the API key was rejected"):
            OpenAICompatible(p).generate(MSGS)
    finally:
        srv.shutdown()
        srv.server_close()
    p = cfg.Provider(name="t", type="openai", base_url=url + "/v1", model="m")      # nothing listens there now
    with pytest.raises(NetError, match="is the server .* running"):
        OpenAICompatible(p).generate(MSGS)


# ---- TLS -----------------------------------------------------------------------------------------------

def test_private_ca_fails_with_advice_then_works_with_ca_bundle(certs):
    srv, url = serve(Api, tls=(certs / "srv.pem", certs / "srv.key"))
    try:
        p = cfg.Provider(name="corp", type="openai", base_url=url + "/v1", model="m",
                         network=cfg.Network(system_certs=False))
        with pytest.raises(NetError, match=r"TLS certificate check failed .*network\.ca_bundle"):
            OpenAICompatible(p).generate(MSGS)
        p.network.ca_bundle = str(certs / "ca.pem")
        assert json.loads(OpenAICompatible(p).generate(MSGS)) == ANSWER
        ctx = ssl_context(p.network)                            # the corporate CA is added, not swapped in
        assert any("Boop Test Root CA" in str(c.get("subject")) for c in ctx.get_ca_certs())
        assert len(ctx.get_ca_certs()) > 10
    finally:
        srv.shutdown()


def test_ca_bundle_from_environment(certs, monkeypatch):
    srv, url = serve(Api, tls=(certs / "srv.pem", certs / "srv.key"))
    try:
        monkeypatch.setenv("SSL_CERT_FILE", str(certs / "ca.pem"))
        p = cfg.Provider(name="corp", type="openai", base_url=url + "/v1", model="m")
        assert json.loads(OpenAICompatible(p).generate(MSGS)) == ANSWER
    finally:
        srv.shutdown()


def test_verify_false_works_but_warns(certs, capsys):
    srv, url = serve(Api, tls=(certs / "srv.pem", certs / "srv.key"))
    try:
        p = cfg.Provider(name="corp", type="openai", base_url=url + "/v1", model="m",
                         network=cfg.Network(verify=False))
        assert json.loads(OpenAICompatible(p).generate(MSGS)) == ANSWER
        assert "certificate checks are OFF" in capsys.readouterr().err
    finally:
        srv.shutdown()


# ---- proxies -------------------------------------------------------------------------------------------

class Proxy(http.server.BaseHTTPRequestHandler):
    """A forwarding HTTP proxy that counts what passes through it."""
    hits = 0

    def do_POST(self):
        import urllib.request
        Proxy.hits += 1
        body = self.rfile.read(int(self.headers["Content-Length"]))
        req = urllib.request.Request(self.path, data=body, method="POST",
                                     headers={k: v for k, v in self.headers.items() if k.lower() != "host"})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as r:
            data = r.read()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


def test_proxy_rules():
    net = cfg.Network(proxy="http://proxy:3128", no_proxy=".corp,internal.example")
    assert proxy_for("https://api.openai.com/v1", net) == "http://proxy:3128"
    assert proxy_for("https://llm.corp/v1", net) is None and proxy_for("https://a.llm.corp", net) is None
    assert proxy_for("http://localhost:11434/v1", net) is None and proxy_for("http://127.0.0.1:1234", net) is None


def test_requests_go_through_the_configured_proxy():
    api, api_url = serve(Api)
    proxy, proxy_url = serve(Proxy)
    Proxy.hits = 0
    try:
        target = api_url.replace("127.0.0.1", "boop-test.invalid")          # not local, so the proxy is used
        p = cfg.Provider(name="t", type="openai", base_url=target + "/v1", model="m",
                         network=cfg.Network(proxy=proxy_url))
        # the proxy resolves boop-test.invalid itself: point it at our API server
        orig = Proxy.do_POST

        def via_local(self):
            self.path = self.path.replace("boop-test.invalid", "127.0.0.1")
            orig(self)
        Proxy.do_POST = via_local
        try:
            assert json.loads(OpenAICompatible(p).generate(MSGS)) == ANSWER
        finally:
            Proxy.do_POST = orig
        assert Proxy.hits == 1
    finally:
        api.shutdown()
        proxy.shutdown()


# ---- Anthropic, through the official SDK ---------------------------------------------------------------

def test_anthropic_via_sdk_with_private_ca(certs):
    pytest.importorskip("anthropic")
    from boop.model import Anthropic
    srv, url = serve(Api, tls=(certs / "srv.pem", certs / "srv.key"))
    try:
        p = cfg.Provider(name="claude", type="anthropic", base_url=url, model="claude-opus-5-5", effort="low",
                         api_key="sk-ant-test", network=cfg.Network(ca_bundle=str(certs / "ca.pem")))
        assert json.loads(Anthropic(p).generate(MSGS)) == ANSWER
        got = Api.seen[-1]
        h = got["headers"]                                       # SDK versions differ in how they send the key
        assert got["path"] == "/v1/messages" and "sk-ant-test" in (h.get("x-api-key", "") + h.get("authorization", ""))
        body = got["body"]
        assert body["system"] == "sys" and body["messages"] == [MSGS[1]] and "temperature" not in body
        assert body["output_config"]["effort"] == "low"
        assert body["output_config"]["format"]["type"] == "json_schema"
        p.network = cfg.Network(system_certs=False)              # and without the CA: a clear TLS error
        with pytest.raises(NetError, match="TLS certificate check failed"):
            Anthropic(p).generate(MSGS)
    finally:
        srv.shutdown()


def test_cli_providers_and_doctor(tmp_path):
    srv, url = serve(Api)
    try:
        cfg.config_path().write_text(f'use = "mine"\n[providers.mine]\nbase_url = "{url}/v1"\nmodel = "m"\n')
        env = {**os.environ, "BOOP_CONFIG": str(cfg.config_path()), "PYTHONPATH": os.getcwd()}
        env.pop("BOOP_BACKEND", None)
        r = subprocess.run([sys.executable, "-m", "boop.cli", "--providers"], capture_output=True, text=True, env=env)
        assert r.returncode == 0 and "● mine" in r.stdout and "anthropic" in r.stdout
        r = subprocess.run([sys.executable, "-m", "boop.cli", "--doctor"], capture_output=True, text=True, env=env)
        assert r.returncode == 0 and "✓ answer  wc -l < notes.txt" in r.stdout, r.stdout + r.stderr
    finally:
        srv.shutdown()
