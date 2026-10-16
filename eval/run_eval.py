"""Does the command actually do the job? Each task runs the model's command in a fresh throwaway folder
and checks the result (output or files), so any correct command passes, not just one exact string.

  python eval/run_eval.py                        # default local model
  BOOP_API_BASE=http://localhost:11434/v1 BOOP_API_MODEL=qwen2.5-coder:7b python eval/run_eval.py
  python eval/run_eval.py --dump eval/out/base.jsonl --only git

macOS and Linux (POSIX shells). Commands the safety rules flag are only executed when the task expects a
risky command, and never when they point outside the sandbox.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from boop.context import Context, detect_os, detect_shell, listing  # noqa: E402
from boop.model import make_backend  # noqa: E402
from boop.prompt import build_messages, parse_answer  # noqa: E402
from boop.run import argv_for  # noqa: E402
from boop.safety import warnings  # noqa: E402

TIMEOUT = 15


def build_fixture(root: Path, git: bool) -> None:
    files = {
        "notes.txt": "hello\nworld\nhello\n",
        "todo.md": "# todo\nTODO: write the docs\n",
        "data.csv": "name,age\nann,31\nbob,25\n",
        "my file.txt": "spaces in the name\n",
        "src/app.py": "print('hi')\n# TODO fix\n",
        "src/util.py": "def f():\n    return 1\n",
        "src/old.js": "console.log('x')\n",
        "logs/app.log": ("2026-10-01 10:00:00 INFO start\n2026-10-01 10:01:00 ERROR db down\n"
                         "2026-10-01 10:02:00 INFO retry ok\n2026-10-01 10:03:00 ERROR db down again\n"),
        "logs/old.log": "old\n",
        "texts/a.txt": "alpha\n",
        "texts/b.txt": "beta\n",
        "images/a.png": "png", "images/b.jpg": "jpg",
        "build/out.o": "obj",
        "docs/small.pdf": "%PDF-1.4 small\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    with open(root / "docs/big.pdf", "wb") as fh:          # real bytes, so du/ls/find -size all agree
        fh.write(b"%PDF" + os.urandom(11 * 1024 * 1024 - 8192))   # just under 11 MiB: du/ls say 11M
    if git:
        g = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
        g("init", "-q", "-b", "main")
        g("config", "user.email", "eval@boop"); g("config", "user.name", "eval")
        g("add", "src"); g("commit", "-q", "-m", "add src")
        g("add", "notes.txt"); g("commit", "-q", "-m", "add notes")


def sandbox_prefixes(sandbox: Path) -> list[str]:
    # macOS temp dirs live under /var, which is a symlink to /private/var: accept both spellings
    return sorted({str(sandbox), os.path.realpath(sandbox)}, key=len, reverse=True)


def norm_lines(text: str, sandbox: Path) -> list[str]:
    """Same answer, any spelling: `./a.pdf`, `a.pdf` and `/full/sandbox/path/a.pdf` all count as `a.pdf`."""
    out = []
    for l in text.splitlines():
        l = l.strip()
        for pre in sandbox_prefixes(sandbox):
            l = l.replace(pre + "/", "")
        if not l:
            continue
        l = re.sub(r"^\./", "", l).rstrip("/")
        out.append(l)
    return out


def check(task: dict, stdout: str, sandbox: Path, shell: str) -> bool:
    kind = task["check"]
    if kind == "lines":
        got = norm_lines(stdout, sandbox)
        return got == task["expect"] if task.get("ordered") else sorted(set(got)) == sorted(task["expect"])
    if kind == "number":
        m = re.search(r"-?\d+", stdout)
        return bool(m) and int(m.group(0)) == task["expect"]
    if kind == "contains":
        return all(s in stdout for s in task["expect"])
    if kind == "first":                                   # e.g. "largest first": the top line must be right
        lines = norm_lines(stdout, sandbox)
        return bool(lines) and task["expect"] in lines[0]
    if kind == "verify":
        r = subprocess.run(["sh", "-c", task["verify"]], cwd=sandbox, capture_output=True, timeout=TIMEOUT)
        return r.returncode == 0
    raise ValueError(kind)


def escapes_sandbox(cmd: str, sandbox: Path) -> bool:
    """Absolute paths are fine when they point inside the sandbox; anything else outside it is not."""
    for pre in sandbox_prefixes(sandbox):
        cmd = cmd.replace(pre, ".")
    return bool(re.search(r"~|\$HOME|\$\{HOME\}|(^|[\s'\"])/(?!dev/null)", cmd))


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("--tasks", default=str(ROOT / "eval/tasks.jsonl"))
    ap.add_argument("--shell", default=None, help="default: your login shell")
    ap.add_argument("--only", default="", help="run tasks whose id contains this")
    ap.add_argument("--dump", help="write every request/command/result to this JSONL")
    a = ap.parse_args()

    if detect_os() == "Windows":
        raise SystemExit("eval runs POSIX shells (macOS/Linux) for now")
    shell = detect_shell(a.shell)
    tasks = [json.loads(l) for l in open(a.tasks) if l.strip()]
    tasks = [t for t in tasks if a.only in t["id"]]
    if not shutil.which("git"):
        tasks = [t for t in tasks if not t.get("git")]
    be = make_backend()
    dump = open(a.dump, "w") if a.dump else None
    passed = danger_ok = danger_total = 0
    t0 = time.time()
    print(f"{len(tasks)} tasks · {detect_os()} · {shell}\n")
    for t in tasks:
        with tempfile.TemporaryDirectory(prefix="boop-eval-") as d:
            sandbox = Path(d) / "work"                          # the folder the command runs in
            sandbox.mkdir()
            build_fixture(sandbox, t.get("git", False))
            ctx = Context(os=detect_os(), shell=shell, cwd=str(sandbox), files=listing(sandbox))
            started = time.time()
            ans = parse_answer(be.generate(build_messages(t["request"], ctx)))
            secs = time.time() - started
            cmd, warns = ans.command, warnings(ans.command, shell, str(sandbox))
            note, out, ok = "", "", False
            if t.get("danger"):
                danger_total += 1
                danger_ok += bool(warns)
            if not cmd:
                note = "no command"
            elif warns and not t.get("danger"):
                note = f"flagged risky: {warns[0]}"
            elif warns and escapes_sandbox(cmd, sandbox):
                note = "risky and points outside the sandbox: not run"
            else:
                # a separate, empty HOME: `~/x` must not quietly land in the sandbox (it wouldn't for you)
                home = Path(d) / "home"                            # deleted with the temp dir
                home.mkdir(exist_ok=True)
                env = {**os.environ, "HOME": str(home)}
                try:
                    r = subprocess.run(argv_for(shell, cmd), cwd=sandbox, capture_output=True, text=True,
                                       timeout=TIMEOUT, env=env, stdin=subprocess.DEVNULL)
                    out = r.stdout
                    ok = check(t, out, sandbox, shell)
                    if not ok:
                        note = (r.stderr.strip().splitlines() or [f"exit {r.returncode}"])[-1][:100]
                except subprocess.TimeoutExpired:
                    note = "timed out"
            passed += ok
            mark = "✓" if ok else "✗"
            print(f"{mark} {t['id']:<16} {secs:4.1f}s  {cmd}" + (f"\n    └ {note}" if note and not ok else ""))
            if dump:
                dump.write(json.dumps({"id": t["id"], "request": t["request"], "command": cmd, "ok": ok,
                                       "warnings": warns, "note": note, "stdout": out[:500]}) + "\n")
    n = len(tasks)
    print(f"\npassed {passed}/{n} = {passed / n:.0%}   risky tasks flagged {danger_ok}/{danger_total}   "
          f"({time.time() - t0:.0f}s)")
    if dump:
        dump.close()


if __name__ == "__main__":
    main()
