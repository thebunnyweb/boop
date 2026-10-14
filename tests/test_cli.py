"""End to end with the fake backend: the real CLI, real shells, no model."""
import json
import os
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
