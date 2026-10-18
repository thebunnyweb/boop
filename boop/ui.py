"""Talking to the person at the keyboard. Always via the terminal device, never stdin/stdout, so boop still
works when stdin is a pipe and when the shell captures stdout.

The look, top to bottom: a welcome box (first step of English mode only), a prompt between two rules,
a spinner while the model thinks, then the answer as `⏺ command` / `⎿ explanation` with the keys below it.
"""
from __future__ import annotations

import itertools
import os
import random
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

IS_WINDOWS = sys.platform.startswith("win")


def _color_ok() -> bool:
    return not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


if IS_WINDOWS:
    os.system("")   # turns on ANSI escape handling in the Windows console


def style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _color_ok() else text


DIM, BOLD, RED, YELLOW, CYAN, GREEN = "2", "1", "31", "33", "36", "32"
ACCENT = "38;5;209"      # boop's warm orange
GREY = "38;5;245"

FANCY = dict(tl="╭", tr="╮", bl="╰", br="╯", h="─", v="│", dot="⏺", hook="⎿", warn="⚠", prompt="›",
             spin="·✢✳✶✻✽✻✶✳✢", face="(•ᴥ•)")
PLAIN = dict(tl="+", tr="+", bl="+", br="+", h="-", v="|", dot="*", hook="`-", warn="!", prompt=">",
             spin="-\\|/", face="(o.o)")

VERBS = ["Sniffing", "Fetching", "Digging", "Pawing through man pages", "Wagging", "Booping", "Tracking",
         "Chasing the right flag", "Nosing around"]
EXAMPLES = ["find pdfs over 10mb in downloads", "undo my last commit but keep the changes", "zip the src folder",
            "what's using port 3000", "count lines in all python files", "show the 5 biggest files here"]
SLASH = {"/help": "shortcuts and examples", "/forget": "clear what boop remembers", "/model": "which model is answering",
         "/exit": "leave English mode"}


class Terminal:
    """Reads keys and lines from the real terminal (/dev/tty or the Windows console)."""

    def __init__(self):
        if IS_WINDOWS:
            self.out = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            self.inp = open("CONIN$", "r", encoding="utf-8", errors="replace")
        else:
            dev = _tty_path()
            self.out = open(dev, "w", encoding="utf-8", errors="replace")
            self.inp = open(dev, "r")
        self.g = FANCY if _can_draw(self.out) else PLAIN

    @staticmethod
    def available() -> bool:
        try:
            Terminal().close()
            return True
        except OSError:
            return False

    def close(self) -> None:
        for f in (self.out, self.inp):
            try:
                f.close()
            except OSError:
                pass

    def write(self, text: str = "", end: str = "\n") -> None:
        self.out.write(text + end)
        self.out.flush()

    @property
    def width(self) -> int:
        try:
            return max(20, os.get_terminal_size(self.out.fileno()).columns)
        except OSError:
            return 80

    def key(self) -> str:
        """One keypress, no Enter needed. Returns '\\n' for Enter, '' for Ctrl-C/Ctrl-D/Esc."""
        if IS_WINDOWS:
            import msvcrt
            ch = msvcrt.getwch()
            if ch in ("\x00", "\xe0"):     # arrow/function keys come as two characters
                msvcrt.getwch()
                return "?"
        else:
            import termios
            import tty
            fd = self.inp.fileno()
            old = termios.tcgetattr(fd)
            try:
                tty.setraw(fd)
                ch = os.read(fd, 1).decode(errors="replace")
            finally:
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if ch in ("\r", "\n"):
            return "\n"
        if ch in ("\x03", "\x04", "\x1b"):
            return ""
        return ch.lower()

    def line(self, prompt: str, prefill: str = "", **kw) -> str | None:
        """A line of input (prompt_toolkit: editing, history, Windows). None on Ctrl-C/Ctrl-D."""
        try:
            return _toolkit_line(self, prompt, prefill, **kw)
        except Exception as e:      # no prompt_toolkit, or it can't drive this terminal: plain input still works
            if os.environ.get("BOOP_DEBUG"):
                self.write(f"[boop: plain input, prompt_toolkit failed: {e!r}]")
        self.out.write(prompt)
        self.out.flush()
        try:
            s = self.inp.readline()
        except KeyboardInterrupt:
            return None
        if not s:
            return None
        s = s.rstrip("\r\n")
        return s if s or not prefill else prefill


def _tty_path() -> str:
    """The terminal's real device (/dev/ttys003, /dev/pts/4), not the /dev/tty alias: macOS kqueue, which
    asyncio and so prompt_toolkit use there, refuses to watch /dev/tty."""
    for fd in (0, 2, 1):
        try:
            if os.isatty(fd):
                return os.ttyname(fd)
        except OSError:
            continue
    return "/dev/tty"


def _can_draw(out) -> bool:
    if os.environ.get("BOOP_ASCII") or os.environ.get("TERM") == "linux":   # the bare Linux console has no ⏺
        return False
    try:
        "╭⏺⎿✻ᴥ".encode(out.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def text_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1 for c in s)


def _fit(s: str, width: int) -> str:
    if text_width(s) <= width:
        return s + " " * (width - text_width(s))
    while text_width(s) > width - 1:
        s = s[:-1]
    return s + "…"


def _home(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


# ---- prompt_toolkit input -----------------------------------------------------------------------

def _history_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (os.environ.get("LOCALAPPDATA") if IS_WINDOWS else None) \
        or str(Path.home() / ".local" / "state")
    return Path(base) / "boop" / "history"


def _toolkit_line(term: Terminal, prompt: str, prefill: str = "", *, history: bool = False,
                  placeholder: str = "", toolbar: str = "", slash: bool = False) -> str | None:
    """One line of input drawn as a small inline layout: the input, then (while typing) the slash-command
    menu and the toolbar right under it. Not PromptSession: its toolbar sticks to the bottom of the terminal."""
    from prompt_toolkit.application import Application
    from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.filters import is_done
    from prompt_toolkit.formatted_text import ANSI, to_formatted_text
    from prompt_toolkit.history import FileHistory, InMemoryHistory
    from prompt_toolkit.input import create_input
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import ConditionalContainer, HSplit, Layout, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.layout.processors import AppendAutoSuggestion, BeforeInput, Processor, Transformation
    from prompt_toolkit.output import create_output
    from prompt_toolkit.styles import Style

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if text.startswith("/") and " " not in text:
                for cmd, what in SLASH.items():
                    if cmd.startswith(text):
                        yield Completion(cmd, start_position=-len(text), display_meta=what)

    class Placeholder(Processor):
        def apply_transformation(self, ti):
            if placeholder and not ti.document.text and ti.lineno == 0:
                return Transformation(ti.fragments + [("class:placeholder", placeholder)])
            return Transformation(ti.fragments)

    hist = InMemoryHistory()
    if history:
        try:
            _history_file().parent.mkdir(parents=True, exist_ok=True)
            hist = FileHistory(str(_history_file()))
        except OSError:
            pass

    def accept(b):
        app.exit(result=b.text)
        return True                         # keep the text (FileHistory still records it)

    buf = Buffer(document=Document(prefill, len(prefill)), history=hist, multiline=False,
                 auto_suggest=AutoSuggestFromHistory() if history else None,
                 completer=SlashCompleter() if slash else None, complete_while_typing=slash,
                 accept_handler=accept)
    keys = KeyBindings()

    @keys.add("c-c")
    def _(event):           # like Claude Code: Ctrl-C clears the line; on an empty line it leaves
        if buf.text:
            buf.reset()
        else:
            event.app.exit(result=None)

    @keys.add("c-d")
    def _(event):
        if not buf.text:
            event.app.exit(result=None)

    @keys.add("enter")
    def _(event):
        state = buf.complete_state
        if state and state.current_completion:
            buf.apply_completion(state.current_completion)
        buf.validate_and_handle()

    @keys.add("tab")
    def _(event):
        if buf.complete_state:
            buf.complete_next()
        else:
            buf.start_completion(select_first=True)

    rows = [Window(BufferControl(buf, input_processors=[BeforeInput(to_formatted_text(ANSI(prompt))),
                                                        AppendAutoSuggestion(), Placeholder()]),
                   wrap_lines=True, dont_extend_height=True),
            CompletionsMenu(max_height=len(SLASH))]
    if toolbar:
        rows.append(ConditionalContainer(Window(FormattedTextControl(ANSI(toolbar)), dont_extend_height=True),
                                         filter=~is_done))
    app = Application(
        layout=Layout(HSplit(rows)), key_bindings=keys, full_screen=False, erase_when_done=False,
        input=create_input(stdin=term.inp), output=create_output(stdout=term.out),
        style=Style.from_dict({"placeholder": "#808080", "auto-suggestion": "#808080",
                               "completion-menu": "bg:default #bbbbbb",
                               "completion-menu.completion.current": "bg:default #ff875f bold",
                               "completion-menu.meta.completion": "bg:default #808080",
                               "completion-menu.meta.completion.current": "bg:default #808080"}))
    try:
        return app.run()                    # None: the person left (Ctrl-D, or Ctrl-C on an empty line)
    except KeyboardInterrupt:
        return None
    except EOFError as e:                   # the input closed under prompt_toolkit: let plain input try
        raise RuntimeError("prompt_toolkit got EOF") from e


# ---- the pieces of the screen ---------------------------------------------------------------------

def welcome(term: Terminal, *, version: str, cwd: str, shell: str, os_name: str, model: str) -> None:
    g = term.g
    w = min(term.width, 64) - 2           # inside the box
    rows = [
        style(g["face"], ACCENT) + " " + style("boop", BOLD) + style(f" v{version}", GREY),
        "",
        "  Say what you want in plain English.",
        style("  /help for shortcuts · exit or Ctrl-D to leave", GREY),
        "",
        style(f"  cwd: {_home(cwd)}", GREY),
        style(f"  {shell} on {os_name} · {model}", GREY),
    ]
    term.write(style(g["tl"] + g["h"] * w + g["tr"], ACCENT))
    for r in rows:
        plain = _strip(r)
        body = r if text_width(plain) <= w - 1 else style(_fit(plain, w - 1), GREY)
        term.write(style(g["v"], ACCENT) + " " + body + " " * max(0, w - 1 - text_width(_strip(body))) +
                   style(g["v"], ACCENT))
    term.write(style(g["bl"] + g["h"] * w + g["br"], ACCENT))


def _strip(s: str) -> str:
    import re
    return re.sub(r"\033\[[0-9;]*m", "", s)


def ask_request(term: Terminal, *, cwd: str, shell: str) -> str | None:
    """The English-mode prompt: a rule, `› …`, a rule and hints. Leaves `› request` on screen afterwards."""
    g, w = term.g, term.width
    rule = style(g["h"] * w, GREY)
    left = "  ↑ history · / commands · Ctrl-C clears · Ctrl-D leaves" if g is FANCY else \
        "  up: history  / commands  Ctrl-C clears  Ctrl-D leaves"
    right = f"{_home(cwd)} · {shell} "
    gap = w - 1 - text_width(left) - text_width(right)
    hints = left + " " * gap + right if gap > 1 else left
    term.write()
    term.write(rule)
    line = term.line(style(g["prompt"] + " ", BOLD), history=True, slash=True,
                     placeholder=f'Try "{random.choice(EXAMPLES)}"',
                     toolbar=rule + "\n" + style(_fit(hints, w - 1).rstrip(), GREY))
    if _color_ok():
        # tidy up: drop the rule and keep the request as a quiet `› request` line, like a chat log
        rows = 1 + (text_width(line or "") + 2) // w
        keep = bool(line and line.strip())                      # nothing typed: drop the blank line too
        term.write(f"\033[{rows + (1 if keep else 2)}A\r\033[J", end="")
        if keep:
            term.write(style(g["prompt"] + " " + line, GREY))
    return line


class Spinner:
    """`✻ Sniffing… (3s · Ctrl-C to stop)` on one line while the model thinks; gone when done."""

    def __init__(self, term: Terminal | None):
        self.term, self._stop = term, threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        if self.term:
            self._thread.start()
        return self

    def __exit__(self, *exc):
        if self.term:
            self._stop.set()
            self._thread.join()
            self.term.write("\r\033[K", end="")

    def _run(self) -> None:
        g, verb, t0 = self.term.g, random.choice(VERBS), time.monotonic()
        for ch in itertools.cycle(g["spin"]):
            head, tail = f"{ch} {verb}…", f" ({int(time.monotonic() - t0)}s · Ctrl-C to stop)"
            room = self.term.width - 1 - text_width(head)          # never wrap: \r only rewinds one line
            self.term.write("\r\033[K" + style(head, ACCENT) + style(tail[:max(0, room)], GREY), end="")
            if self._stop.wait(0.12):
                return


def note(term: Terminal, text: str, code: str = GREY) -> None:
    term.write("  " + style(f"{term.g['hook']}  {text}", code))


def help_screen(term: Terminal) -> None:
    term.write("  " + style("Just type what you want. A few ideas:", BOLD))
    for e in EXAMPLES[:4]:
        term.write("    " + style(e, GREY))
    term.write()
    term.write("  " + style("Commands", BOLD))
    for cmd, what in SLASH.items():
        term.write(f"    {cmd:<9}" + style(what, GREY))
    term.write()
    term.write("  " + style("Keys", BOLD))
    for k, what in [("↑ / ↓", "earlier requests"), ("→", "accept the grey suggestion"),
                    ("Ctrl-C", "clear the line (on an empty line: leave)"), ("Ctrl-D", "leave")]:
        term.write(f"    {k:<9}" + style(what, GREY))


def copy_to_clipboard(text: str) -> bool:
    candidates = (
        [["clip"]] if IS_WINDOWS else
        [["pbcopy"]] if sys.platform == "darwin" else
        [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    )
    for cmd in candidates:
        try:
            subprocess.run(cmd, input=text.encode("utf-16le" if IS_WINDOWS else "utf-8"), check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except (OSError, subprocess.CalledProcessError):
            continue
    return False


# ---- the confirm screen -----------------------------------------------------------------------------

RUN, CANCEL = "run", "cancel"


def show_answer(term: Terminal, command: str, explanation: str, warns: list[str]) -> None:
    g = term.g
    term.write(style(g["dot"], ACCENT) + " " + style(command, BOLD))
    if explanation:
        note(term, explanation)
    for w in warns:
        term.write("  " + style(f"{g['warn']}  {w}", RED))


def _erase_line() -> str:
    return "\033[1A\r\033[K" if _color_ok() else ""


def confirm(term: Terminal, command: str, explanation: str, shell: str) -> tuple[str, str]:
    """Show the command and let the person run / edit / copy / cancel. Returns (RUN|CANCEL, command).
    Risky commands (see safety.py) need the word 'yes', not just Enter."""
    from .safety import warnings as check

    while True:
        warns = check(command, shell, os.getcwd())
        show_answer(term, command, explanation, warns)
        if warns:
            ans = term.line(style("  type yes to run · e to edit · anything else cancels › ", YELLOW))
            ans = (ans or "").strip().lower()
            k = "\n" if ans == "yes" else "e" if ans == "e" else ""
            term.write(_erase_line(), end="")
        else:
            term.write(style("  ⏎ run · e edit · c copy · esc cancel", GREY), end="")
            k = term.key()
            term.write("\r\033[K" if _color_ok() else "", end="" if _color_ok() else "\n")
        if k == "\n":
            return RUN, command
        if k == "e":
            new = term.line(style("  edit › ", YELLOW), prefill=command)
            if new is None or not new.strip():
                note(term, "cancelled")
                return CANCEL, command
            if new.strip() != command:
                command, explanation = new.strip(), "edited by you"
            continue
        if k == "c":
            ok = copy_to_clipboard(command)
            note(term, "copied to the clipboard" if ok else "couldn't find a clipboard tool", GREY if ok else RED)
            return CANCEL, command
        note(term, "cancelled")
        return CANCEL, command
