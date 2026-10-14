"""boop — nudge your shell.

  boop find pdfs over 10mb in downloads     one request → command → [⏎] run
  boop                                      English mode until you type exit
  cat app.log | boop count errors per hour  pipe data in
  boop -p list big files                    just print the command (scripts, `| sh`)
  boop -x 'tar -xzvf a.tgz'                 explain a command
  boop --init zsh                           shell integration (cd/history work, Alt+Enter hotkey)
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import sys
import tempfile

from . import __version__
from .context import Context, detect_shell, forget, load_history, remember
from .prompt import Answer, build_explain_messages, build_messages, parse_answer
from .safety import is_dangerous, warnings

PIPE_SAMPLE_LINES = 20
PIPE_SAMPLE_CHARS = 2000
QUIT = 10           # tells the shell function to leave English mode
_STAYS_IN_SUBSHELL = re.compile(r"^\s*(cd|pushd|popd|export|unset|source|alias|set|Set-Location|\.)\b", re.I)

_backend = None


def backend():
    global _backend
    if _backend is None:
        try:
            from .model import make_backend
            _backend = make_backend()
        except ImportError as e:
            raise SystemExit(f"boop: {e.name or 'llama-cpp-python'} is not installed. "
                             "Try: pipx install boop-cli   (or set BOOP_API_BASE to use Ollama/LM Studio)")
    return _backend


def _status(term, text: str) -> None:
    if term:
        term.write(text, end="")
    else:
        print(text, end="", file=sys.stderr, flush=True)


def _clear(term) -> None:
    from .ui import IS_WINDOWS
    seq = "\r" + " " * 40 + "\r" if IS_WINDOWS else "\r\033[K"
    _status(term, seq)


def ask(request: str, ctx: Context, term=None) -> Answer:
    from .ui import DIM, style
    _status(term, style("  boop: thinking…", DIM))
    try:
        return parse_answer(backend().generate(build_messages(request, ctx)))
    finally:
        _clear(term)


def explain(command: str, shell: str) -> int:
    ctx = Context.gather(shell)
    ans = parse_answer(backend().generate(build_explain_messages(command, ctx)))
    print(ans.explanation or "(no explanation)")
    for w in warnings(command, shell):
        print(f"⚠ {w}")
    return 0


def stdin_is_piped() -> bool:
    """True for `cmd | boop` and `boop < file`; False for a terminal or /dev/null (cron, scripts, hotkeys)."""
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (AttributeError, OSError, ValueError):
        return False
    return stat.S_ISFIFO(mode) or stat.S_ISREG(mode)


def read_pipe() -> tuple[tempfile.SpooledTemporaryFile, str]:
    """Keep all piped data for the command; give the model only a peek at it."""
    spool = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    shutil.copyfileobj(sys.stdin.buffer, spool)
    spool.seek(0)
    head = spool.read(PIPE_SAMPLE_CHARS * 2).decode("utf-8", errors="replace")
    spool.seek(0)
    sample = "\n".join(head.splitlines()[:PIPE_SAMPLE_LINES])[:PIPE_SAMPLE_CHARS]
    return spool, sample


def handle(request: str, shell: str, *, out: str | None, yes: bool, term, spool=None, sample=None) -> int:
    """One request, start to finish. Returns an exit code (see shells.py for the --out protocol)."""
    from .run import run
    from .ui import RED, RUN, confirm, style

    ctx = Context.gather(shell, piped=sample, history=load_history())
    ans = ask(request, ctx, term)
    if not ans.command:
        msg = "  boop: " + (ans.explanation or "couldn't come up with a command for that.")
        if term:
            term.write(style(msg, RED))
        else:
            print(msg, file=sys.stderr)
        return 1
    cmd = ans.command
    if yes and not is_dangerous(cmd, shell, os.getcwd()):
        action = RUN
        if term:
            term.write("  " + cmd)
    elif term is None:
        print(f"boop: {cmd}\nboop: no terminal to confirm on; use -y to run safe commands without asking.",
              file=sys.stderr)
        return 1
    else:
        action, cmd = confirm(term, cmd, ans.explanation, shell)
    if action != RUN:
        return 1
    remember(request, cmd)
    if spool is not None:                       # pipe mode: feed the data to the command ourselves
        return run(cmd, shell, stdin=spool)
    if out:                                     # shell integration runs it in your shell
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(cmd)
        return 0
    if _STAYS_IN_SUBSHELL.match(cmd):
        print(f"boop: note: `{cmd.split()[0]}` won't stick after boop exits. "
              f"For that, add the shell integration: boop --init {shell}", file=sys.stderr)
    return run(cmd, shell)


def english_mode_step(shell: str, out: str | None, banner: bool) -> int:
    from .ui import DIM, GREEN, Terminal, style

    term = Terminal()
    try:
        if banner:
            term.write(style("boop English mode — say what you want. 'exit' or Ctrl-D to leave.", DIM))
        line = term.line(style("boop› ", GREEN))
        if line is None or line.strip().lower() in ("exit", "quit", "q", ":q"):
            if line is None:
                term.write()
            return QUIT
        if not line.strip():
            return 1
        return handle(line.strip(), shell, out=out, yes=False, term=term)
    finally:
        term.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="boop", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("words", nargs=argparse.REMAINDER, help="what you want, in plain English")
    ap.add_argument("--request", help="the request as one argument (used by the hotkeys)")
    ap.add_argument("-p", "--print", action="store_true", help="only print the command; don't run it")
    ap.add_argument("-y", "--yes", action="store_true", help="run without asking (risky commands still ask)")
    ap.add_argument("-x", "--explain", metavar="COMMAND", help="explain a command in plain English")
    ap.add_argument("--shell", help="zsh | bash | fish | powershell | cmd (default: detected)")
    ap.add_argument("--init", metavar="SHELL", help="print the shell integration for zsh/bash/fish/powershell")
    ap.add_argument("--forget", action="store_true", help="clear what boop remembers from this session")
    ap.add_argument("--version", action="version", version=f"boop {__version__}")
    ap.add_argument("--out", help=argparse.SUPPRESS)
    ap.add_argument("--repl", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--banner", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args(argv)

    if a.init:
        from .shells import init_script
        print(init_script(a.init))
        return 0
    if a.forget:
        forget()
        return 0
    shell = detect_shell(a.shell)

    try:
        if a.explain:
            return explain(a.explain, shell)
        if a.repl:
            return english_mode_step(shell, a.out, a.banner)

        request = (a.request or " ".join(a.words)).strip()
        piped = stdin_is_piped()
        if not request:
            if piped or not sys.stdout.isatty():
                ap.print_usage(sys.stderr)
                return 2
            banner = True                           # no shell integration: run English mode right here
            while english_mode_step(shell, None, banner) != QUIT:
                banner = False
            return 0

        spool = sample = None
        if piped:
            spool, sample = read_pipe()

        if a.print:                                 # hotkeys and scripts: command on stdout, nothing else
            ctx = Context.gather(shell, piped=sample, history=load_history())
            ans = ask(request, ctx, term=None)
            if not ans.command:
                print(f"boop: {ans.explanation or 'no command for that'}", file=sys.stderr)
                return 1
            print(ans.command)
            for w in warnings(ans.command, shell, os.getcwd()):
                print(f"⚠ {w}", file=sys.stderr)
            return 0

        from .ui import Terminal
        term = Terminal() if Terminal.available() else None
        try:
            return handle(request, shell, out=a.out, yes=a.yes, term=term, spool=spool, sample=sample)
        finally:
            if term:
                term.close()
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
