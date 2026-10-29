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
import threading

from . import __version__
from .context import Context, detect_shell, forget, load_history, remember
from .prompt import Answer, build_explain_messages, build_messages, parse_answer, with_alternatives
from .net import NetError
from .safety import is_dangerous, warnings

PIPE_SAMPLE_LINES = 20
PIPE_SAMPLE_CHARS = 2000
QUIT = 10           # tells the shell function to leave English mode
QUIT_WORDS = ("exit", "quit", "q", ":q")
_STAYS_IN_SUBSHELL = re.compile(r"^\s*(cd|pushd|popd|export|unset|source|alias|set|Set-Location|\.)\b", re.I)

_backend = None
_backend_lock = threading.Lock()      # the panel preloads the model in a thread while you type


def backend():
    with _backend_lock:
        return _load_backend()


def _load_backend():
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


def context_for(request: str, shell: str, *, sample=None) -> Context:
    """Everything the model sees besides the request, including folders the request names."""
    from . import index
    ctx = Context.gather(shell, piped=sample, history=load_history())
    try:
        ctx.known = index.lookup(request, local=ctx.files)
    except Exception:                       # a broken index must never break boop
        ctx.known = []
    return ctx


def ask(request: str, ctx: Context, term=None) -> Answer:
    from .ui import DIM, Spinner, style
    if term is None:                        # -p and hotkeys: a quiet note on stderr
        _status(None, style("  boop: thinking…", DIM))
        try:
            return parse_answer(backend().generate(build_messages(request, ctx)))
        finally:
            _clear(None)
    with Spinner(term):
        return parse_answer(backend().generate(build_messages(request, ctx)))


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


def execute(request: str, cmd: str, shell: str, *, out: str | None, spool=None) -> int:
    """Run a chosen command: in your shell via --out, with the piped data, or ourselves."""
    from .picker import record_run
    from .run import run

    remember(request, cmd)
    record_run(request, cmd)
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


def interactive(term, shell: str, *, out: str | None, request: str = "", english: bool = False,
                spool=None, sample=None) -> int:
    """The panel: type (or arrive with) a request, pick a command, run it. Falls back to plain prompts.
    In English mode, leaving the panel (esc, Ctrl-D) ends English mode."""
    from . import picker, ui
    from .model import model_label, needs_download

    if not ui.panel_ok(term):
        return plain(term, shell, out=out, request=request, english=english, spool=spool, sample=sample)
    if needs_download():                        # first run: let the download show its progress
        term.write(ui.style("boop: downloading the model once…", ui.GREY))
        backend()
    from .index import refresh_in_background
    refresh_in_background()

    def ask_model(req: str) -> Answer:
        ctx = context_for(req, shell, sample=sample)
        return with_alternatives(parse_answer(backend().generate(build_messages(req, ctx))), ctx.known)
    try:
        res = picker.pick(term, ask=ask_model, warnings_for=lambda c: warnings(c, shell, os.getcwd()),
                          cwd=os.getcwd(), shell=shell, model=model_label().removesuffix(" (local)"),
                          query=request, auto_ask=bool(request), preload=backend)
    except Exception as e:                      # a terminal prompt_toolkit can't drive: plain prompts still work
        if os.environ.get("BOOP_DEBUG"):
            term.write(f"[boop: panel failed: {e!r}]")
        return plain(term, shell, out=out, request=request, english=english, spool=spool, sample=sample)
    if res is None:
        return QUIT if english else 1
    if res.action == "copy":
        ok = ui.copy_to_clipboard(res.command)
        ui.trail(term, res.request, res.command, "copied" if ok else "couldn't find a clipboard tool")
        return 1
    ui.trail(term, res.request, res.command)
    return execute(res.request, res.command, shell, out=out, spool=spool)


def plain(term, shell: str, *, out: str | None, request: str = "", english: bool = False,
          spool=None, sample=None) -> int:
    """Line-by-line version of the panel, for dumb terminals and BOOP_PLAIN=1."""
    from .ui import ACCENT, RED, RUN, confirm, note, style

    if not request:
        line = term.line(style("boop ❯ " if term.fancy else "boop> ", ACCENT))
        if line is None or line.strip().lower() in QUIT_WORDS:
            return QUIT if english else 1
        request = line.strip()
        if not request:
            return 1
    ans = ask(request, context_for(request, shell, sample=sample), term)
    if not ans.command:
        note(term, ans.explanation or "couldn't come up with a command for that.", RED)
        return 1
    action, cmd = confirm(term, ans.command, ans.explanation, shell)
    if action != RUN:
        return 1
    return execute(request, cmd, shell, out=out, spool=spool)


def run_without_asking(request: str, shell: str, *, out: str | None, term, spool=None, sample=None) -> int:
    """-y: safe commands run straight away; risky ones still go through the panel."""
    from .ui import RED, note, show_answer

    ans = ask(request, context_for(request, shell, sample=sample), term)
    if not ans.command:
        msg = ans.explanation or "couldn't come up with a command for that."
        if term:
            note(term, msg, RED)
        else:
            print(f"boop: {msg}", file=sys.stderr)
        return 1
    if is_dangerous(ans.command, shell, os.getcwd()):
        if term is None:
            print(f"boop: {ans.command}\nboop: no terminal to confirm on; risky commands always ask.",
                  file=sys.stderr)
            return 1
        return interactive(term, shell, out=out, request=request, spool=spool, sample=sample)
    if term:
        show_answer(term, ans.command, ans.explanation, [])
    return execute(request, ans.command, shell, out=out, spool=spool)


def english_mode_step(shell: str, out: str | None) -> int:
    from .ui import Terminal

    term = Terminal()
    try:
        return interactive(term, shell, out=out, english=True)
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
    ap.add_argument("--reindex", action="store_true", help="rebuild the folder index now (it updates daily)")
    ap.add_argument("--use", metavar="PROVIDER", help="answer with this provider (from the config or a preset)")
    ap.add_argument("--providers", action="store_true", help="list providers and which one is in use")
    ap.add_argument("--doctor", action="store_true", help="test the provider: key, TLS, proxy, a real request")
    ap.add_argument("--init-config", action="store_true", help="write a commented starter config file")
    ap.add_argument("--download", action="store_true", help="download the local model now, for offline use later")
    ap.add_argument("--version", action="version", version=f"boop {__version__}")
    ap.add_argument("--out", help=argparse.SUPPRESS)
    ap.add_argument("--repl", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--banner", action="store_true", help=argparse.SUPPRESS)   # old shell scripts pass it
    a = ap.parse_args(argv)

    if a.init:
        from .shells import init_script
        print(init_script(a.init))
        return 0
    if a.forget:
        forget()
        return 0
    if a.use:
        os.environ["BOOP_PROVIDER"] = a.use          # also reaches the background model loader and subprocesses
    if a.providers or a.doctor:
        from . import doctor
        return doctor.providers(a.use) if a.providers else doctor.doctor(a.use)
    if a.download:
        from . import config as cfg
        from .model import model_file
        p = cfg.active(a.use)
        if p.type != "llamacpp":
            print(f"boop: provider {p.name} is {p.type}: nothing to download (it needs its server or the internet)")
            return 0
        print(f"boop: model ready, works offline: {model_file(p.model, p.file, p.network, download=True)}")
        return 0
    if a.init_config:
        from .config import write_starter
        path, made = write_starter()
        print(f"boop: {'wrote' if made else 'already exists:'} {path}")
        return 0
    if a.reindex:
        from .index import build, denied_hint, index_file
        print(f"boop: indexed {build()} folders → {index_file()}")
        hint = denied_hint()
        if hint:
            print("boop: " + hint, end="", file=sys.stderr)
        return 0
    shell = detect_shell(a.shell)

    try:
        if a.explain:
            return explain(a.explain, shell)
        if a.repl:
            return english_mode_step(shell, a.out)

        request = (a.request or " ".join(a.words)).strip()
        piped = stdin_is_piped()
        if not request:
            if piped or not sys.stdout.isatty():
                ap.print_usage(sys.stderr)
                return 2
            while english_mode_step(shell, None) != QUIT:   # no shell integration: English mode right here
                pass
            return 0

        spool = sample = None
        if piped:
            spool, sample = read_pipe()

        if a.print:                                 # hotkeys and scripts: command on stdout, nothing else
            ans = ask(request, context_for(request, shell, sample=sample), term=None)
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
            if a.yes or term is None:
                return run_without_asking(request, shell, out=a.out, term=term, spool=spool, sample=sample)
            return interactive(term, shell, out=a.out, request=request, spool=spool, sample=sample)
        finally:
            if term:
                term.close()
    except KeyboardInterrupt:
        print(file=sys.stderr)
        return 130
    except NetError as e:
        print(f"boop: {e}", file=sys.stderr)
        return 1


def entry() -> None:
    try:
        sys.exit(main())
    except BrokenPipeError:                         # `boop --providers | head`
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)


if __name__ == "__main__":
    entry()
