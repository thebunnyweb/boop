"""End to end with the fake backend: the real CLI, real shells, no model."""
import json
import os
import re
import shutil
import subprocess
import sys

import pytest

FAKE = {
    "pdf": "find . -name '*.pdf'",
    "count error": "grep -c ERROR",
    "go to tmp": "cd /tmp",
    "delete logs": "rm -f *.log",
    "nothing": "",
}


def boop(*args, input=None, env=None):
    e = {**os.environ, "BOOP_BACKEND": "fake", "BOOP_FAKE": json.dumps(FAKE), "BOOP_SESSION": "test", **(env or {})}
    return subprocess.run([sys.executable, "-m", "boop.cli", *args], input=input, capture_output=True, text=True,
                          env=e, stdin=None if input is not None else subprocess.DEVNULL)


def test_print_mode_prints_only_the_command():
    r = boop("-p", "--shell", "bash", "find", "pdf", "files")
    assert r.returncode == 0 and r.stdout.strip() == "find . -name '*.pdf'"


def test_print_mode_warns_on_stderr():
    r = boop("-p", "--shell", "zsh", "--request=delete logs")
    assert r.stdout.strip() == "rm -f *.log" and "deletes files" in r.stderr


def test_no_command_is_an_error():
    r = boop("-p", "--shell", "bash", "nothing", "here")
    assert r.returncode == 1 and r.stdout == ""


def test_out_protocol_writes_command_for_the_shell(tmp_path):
    out = tmp_path / "out"
    r = boop("-y", "--shell", "bash", "--out", str(out), "find", "pdf")
    assert r.returncode == 0 and out.read_text() == "find . -name '*.pdf'"


def test_risky_command_never_runs_without_a_terminal(tmp_path):
    out = tmp_path / "out"
    r = boop("-y", "--shell", "bash", "--out", str(out), "delete", "logs")
    assert r.returncode == 1 and not out.exists() and "no terminal" in r.stderr


def test_pipe_mode_feeds_data_to_the_command():
    r = boop("-y", "--shell", "bash", "count", "errors", input="ERROR a\nINFO b\nERROR c\n")
    assert r.returncode == 0 and r.stdout.strip() == "2"


def test_init_scripts():
    for sh, needle in [("zsh", "bindkey"), ("bash", "bind -x"), ("fish", "commandline"),
                       ("powershell", "Set-PSReadLineKeyHandler"), ("cmd", "nothing to install")]:
        r = boop("--init", sh)
        assert r.returncode == 0 and needle in r.stdout, sh


@pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")
def test_bash_integration_runs_in_the_current_shell(tmp_path):
    """The whole point of the shell function: `cd` sticks and the command lands in history."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "boop").write_text(f"#!/bin/sh\nexec {sys.executable} -m boop.cli \"$@\"\n")
    (bin_dir / "boop").chmod(0o755)
    script = 'eval "$(boop --init bash)"; cd /; boop -y go to tmp; pwd'
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "BOOP_BACKEND": "fake",
           "BOOP_FAKE": json.dumps(FAKE), "PYTHONPATH": os.getcwd()}
    r = subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] in ("/tmp", os.path.realpath("/tmp"))


def _in_a_terminal(args, keys, env, cwd):
    """Run boop on a real pseudo-terminal, type `keys` one chunk at a time, return (exit code, screen bytes)."""
    import pty
    import select
    import time

    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(cwd)
        os.execvpe(sys.executable, [sys.executable, "-m", "boop.cli", *args], env)
    seen, pending = b"", list(keys)

    def pump(seconds):
        nonlocal seen
        end = time.time() + seconds
        while time.time() < end:
            if not select.select([fd], [], [], 0.05)[0]:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                return
            if b"\x1b[6n" in data:                      # prompt_toolkit asks where the cursor is
                os.write(fd, b"\x1b[1;1R")
            seen += data

    pump(1.5)
    while pending:
        os.write(fd, pending.pop(0).encode())
        pump(0.8)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), seen


@pytest.mark.skipif(sys.platform.startswith("win"), reason="needs a POSIX pty")
def test_english_mode_step_in_a_real_terminal(tmp_path):
    out = tmp_path / "out"
    env = {**os.environ, "BOOP_BACKEND": "fake", "BOOP_FAKE": json.dumps(FAKE), "BOOP_SESSION": "pty",
           "TERM": "xterm-256color", "XDG_STATE_HOME": str(tmp_path / "state"), "PYTHONPATH": os.getcwd()}
    rc, screen = _in_a_terminal(["--shell", "bash", "--repl", "--banner", "--out", str(out)],
                                ["find pdf files\r", "\r"], env, tmp_path)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", screen.decode(errors="replace"))
    assert rc == 0 and out.read_text() == "find . -name '*.pdf'"
    assert "boop v" in text and "⏺" in text and "fake answer for 'pdf'" in text
    assert (tmp_path / "state" / "boop" / "history").read_text().strip().endswith("+find pdf files")


@pytest.mark.skipif(sys.platform.startswith("win"), reason="needs a POSIX pty")
def test_ctrl_d_leaves_english_mode(tmp_path):
    env = {**os.environ, "BOOP_BACKEND": "fake", "TERM": "xterm-256color", "XDG_STATE_HOME": str(tmp_path),
           "PYTHONPATH": os.getcwd()}
    rc, _ = _in_a_terminal(["--shell", "bash", "--repl", "--out", str(tmp_path / "out")], ["\x04"], env, tmp_path)
    assert rc == 10
