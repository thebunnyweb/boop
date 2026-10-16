import json

from boop.context import Context, detect_shell, listing
from boop.prompt import build_messages, family, parse_answer


def ctx(shell="zsh", **kw):
    return Context(os="macOS", shell=shell, cwd="/Users/me/proj", files=["src/", "README.md"], **kw)


def test_messages_carry_context_and_examples_in_the_right_dialect():
    msgs = build_messages("find big files", ctx())
    assert msgs[0]["role"] == "system"
    assert "Request: find big files" in msgs[-1]["content"]
    assert "Folder contains: src/, README.md" in msgs[-1]["content"]
    assert "lsof" in msgs[2]["content"]                          # posix example
    ps = build_messages("find big files", ctx("powershell"))
    assert "Get-NetTCPConnection" in ps[2]["content"]
    assert "Shell: PowerShell" in ps[-1]["content"]


def test_history_and_pipe_sample_are_shown():
    m = build_messages("now zip them", ctx(history=[{"request": "find pdfs", "command": "find . -name '*.pdf'"}],
                                           piped="ERROR a\nINFO b"))[-1]["content"]
    assert "find pdfs  →  find . -name '*.pdf'" in m
    assert "| ERROR a" in m and "piped data on stdin" in m


def test_parse_answer_json_and_fallbacks():
    a = parse_answer(json.dumps({"command": "ls -la", "explanation": "lists"}))
    assert (a.command, a.explanation) == ("ls -la", "lists")
    assert parse_answer("```bash\ndu -sh *\n```").command == "du -sh *"
    assert parse_answer("git status").command == "git status"
    assert parse_answer("").command == ""


def test_family():
    assert family("zsh") == family("fish") == "posix"
    assert family("powershell") == "powershell" and family("cmd") == "cmd"


def test_detect_shell(monkeypatch):
    monkeypatch.delenv("BOOP_SHELL", raising=False)
    assert detect_shell("pwsh") == "powershell"
    monkeypatch.setenv("BOOP_SHELL", "fish")
    assert detect_shell() == "fish"


def test_listing_skips_dotfiles_and_caps(tmp_path):
    for i in range(50):
        (tmp_path / f"f{i:02d}.txt").write_text("x")
    (tmp_path / ".secret").write_text("x")
    (tmp_path / "dir").mkdir()
    out = listing(tmp_path, limit=5)
    assert out[0] == "dir/" and out[-1] == "…" and ".secret" not in out


def test_examples_do_not_leak_into_the_eval():
    """A few-shot example that matches an eval task lets the model copy the answer."""
    from pathlib import Path
    from boop.prompt import _EXAMPLES
    tasks = [json.loads(l)["request"].lower() for l in
             (Path(__file__).parents[1] / "eval/tasks.jsonl").read_text().splitlines() if l.strip()]
    for examples in _EXAMPLES.values():
        for req, ans in examples:
            ex = set(req.lower().split())
            for t in tasks:
                words = set(t.split())
                assert len(ex & words) / len(words) < 0.6, (req, t)
