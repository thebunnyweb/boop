"""boop with the network unplugged: the local model, the index and the history must all keep working."""
import json
import socket
import sys
import types

import pytest

from boop import model as boop_model
from boop.net import NetError


@pytest.fixture
def offline(monkeypatch):
    """Every outgoing connection fails, as on a plane or an air-gapped machine."""
    for k in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(k, raising=False)          # a laptop on a plane has no proxy either

    def refuse(*a, **k):
        raise OSError("network is unreachable (test: offline)")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


@pytest.fixture
def fake_llama(monkeypatch):
    """llama.cpp stand-in: records how it was opened and answers like the model would."""
    opened = []

    class Llama:
        def __init__(self, model_path=None, **kw):
            opened.append(model_path)

        @classmethod
        def from_pretrained(cls, *a, **k):            # the online path boop must not use any more
            raise AssertionError("from_pretrained lists the repo online; boop must load the cached file directly")

        def create_chat_completion(self, **kw):
            return {"choices": [{"message": {"content": json.dumps({"command": "ls", "explanation": "x"})}}]}

    monkeypatch.setitem(sys.modules, "llama_cpp", types.SimpleNamespace(Llama=Llama))
    return opened


def hf_cache(tmp_path, monkeypatch, repo="bunnyweb/boop-360m-gguf", file="m.gguf"):
    """A real Hugging Face cache layout, as left behind by an earlier download."""
    home = tmp_path / "hf"
    snap = home / "hub" / f"models--{repo.replace('/', '--')}" / "snapshots" / "abc123"
    snap.mkdir(parents=True)
    (snap / file).write_bytes(b"GGUF" + b"\0" * 100)
    (home / "hub" / f"models--{repo.replace('/', '--')}" / "refs").mkdir()
    (home / "hub" / f"models--{repo.replace('/', '--')}" / "refs" / "main").write_text("abc123")
    monkeypatch.setenv("HF_HOME", str(home))
    monkeypatch.setenv("HF_HUB_CACHE", str(home / "hub"))
    import huggingface_hub.constants as c
    monkeypatch.setattr(c, "HF_HUB_CACHE", str(home / "hub"), raising=False)
    return snap / file


def test_cached_model_loads_and_answers_offline(tmp_path, monkeypatch, offline, fake_llama):
    path = hf_cache(tmp_path, monkeypatch)
    be = boop_model.LlamaCpp("bunnyweb/boop-360m-gguf", "m.gguf")
    assert fake_llama == [str(path)]
    assert json.loads(be.generate([{"role": "user", "content": "x"}]))["command"] == "ls"


def test_not_downloaded_offline_says_what_to_do_quickly(tmp_path, monkeypatch, offline, fake_llama):
    import time
    hf_cache(tmp_path, monkeypatch, repo="someone/other-model")
    t0 = time.time()
    with pytest.raises(NetError, match=r"can't be reached.*boop --download"):
        boop_model.LlamaCpp("bunnyweb/boop-360m-gguf", "m.gguf")
    assert time.time() - t0 < 5                       # not the hub's ~20 s of retries


def test_a_gguf_file_path_needs_no_hub_at_all(tmp_path, offline, fake_llama):
    f = tmp_path / "copied-over.gguf"
    f.write_bytes(b"GGUF")
    boop_model.LlamaCpp(str(f))
    assert fake_llama == [str(f)]
    with pytest.raises(NetError, match="does not exist"):
        boop_model.model_file(str(tmp_path / "missing.gguf"))


def test_folder_index_and_history_are_offline(tmp_path, monkeypatch, offline):
    from boop import index, picker
    (tmp_path / "home" / "Media" / "Anime").mkdir(parents=True)
    monkeypatch.setenv("BOOP_INDEX_ROOTS", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert index.build() >= 2
    assert index.lookup("pdfs in my anime folder", index=index.load(), home=str(tmp_path / "home"))
    picker.record_run("list files", "ls")
    assert picker.load_runs()[0]["command"] == "ls"
