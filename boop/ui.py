"""Talking to the person at the keyboard. Always via the terminal device, never stdin/stdout, so boop still
works when stdin is a pipe and when the shell captures stdout.

The panel (picker.py) is the normal interface. What's here is the terminal plumbing, the line boop leaves
behind after a run, and a plain line-by-line fallback for terminals the panel can't drive.
"""
from __future__ import annotations

import itertools
import os
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


class Terminal:
    """Reads keys and lines from the real terminal (its tty device, or the Windows console)."""

    def __init__(self):
        if IS_WINDOWS:
            self.out = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            self.inp = open("CONIN$", "r", encoding="utf-8", errors="replace")
        else:
            dev = _tty_path()
            self.out = open(dev, "w", encoding="utf-8", errors="replace")
            self.inp = open(dev, "r")
        self.fancy = _can_draw(self.out)

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

    def line(self, prompt: str, prefill: str = "") -> str | None:
        """A plain line of input. None on Ctrl-C/Ctrl-D."""
        if prefill:
            self.write(style(f"  (now: {prefill})", GREY))
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
    if os.environ.get("BOOP_ASCII") or os.environ.get("TERM") == "linux":   # the bare Linux console
        return False
    try:
        "╭─│❯▌⠋✦⚠".encode(out.encoding or "ascii")
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def panel_ok(term: Terminal) -> bool:
    """The panel needs prompt_toolkit, colours and box-drawing characters; BOOP_PLAIN=1 turns it off."""
    if os.environ.get("BOOP_PLAIN") or not _color_ok() or not term.fancy:
        return False
    try:
        import prompt_toolkit  # noqa: F401
        return True
    except ImportError:
        return False


def text_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 0 if unicodedata.combining(c) else 1 for c in s)


def home_path(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path == home or path.startswith(home + os.sep) else path


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


def trail(term: Terminal, request: str, command: str, note: str = "") -> None:
    """What stays on screen after the panel closes: one line, the command coloured like in the panel."""
    try:
        from prompt_toolkit import print_formatted_text
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.output import create_output
        from prompt_toolkit.styles import Style

        from .highlight import tokens
        from .picker import THEME
        frags = [("class:prompt", "❯ ")] + tokens(command) + ([("class:dim", f"  {note}")] if note else [])
        print_formatted_text(FormattedText(frags), style=Style.from_dict(THEME), output=create_output(stdout=term.out))
        return
    except Exception:
        pass
    arrow = "❯" if term.fancy else ">"
    term.write(style(f"{arrow} ", ACCENT) + style(command, BOLD) + (style(f"  {note}", GREY) if note else ""))


# ---- plain fallback: line by line, no panel -----------------------------------------------------

class Spinner:
    """`⠋ asking…` on one line while the model thinks; gone when done."""

    def __init__(self, term: Terminal | None, label: str = "asking"):
        self.term, self.label, self._stop = term, label, threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def __enter__(self):
        if self.term:
            self._thread.start()
        return self

    def __exit__(self, *exc):
        if self.term:
            self._stop.set()
            self._thread.join()
            self.term.write("\r\033[K" if _color_ok() else "\n", end="")

    def _run(self) -> None:
        frames, t0 = ("⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏" if self.term.fancy else "-\\|/"), time.monotonic()
        for ch in itertools.cycle(frames):
            text = f"  {ch} {self.label}… {time.monotonic() - t0:.0f}s"
            self.term.write("\r\033[K" + style(text[:self.term.width - 1], GREY) if _color_ok() else "", end="")
            if self._stop.wait(0.1):
                return


def note(term: Terminal, text: str, code: str = GREY) -> None:
    term.write("    " + style(text, code))


RUN, CANCEL = "run", "cancel"


def show_answer(term: Terminal, command: str, explanation: str, warns: list[str]) -> None:
    term.write("  " + style("→ " if term.fancy else "> ", ACCENT) + style(command, BOLD))
    if explanation:
        note(term, explanation)
    for w in warns:
        note(term, ("⚠ " if term.fancy else "! ") + w, RED)


def confirm(term: Terminal, command: str, explanation: str, shell: str) -> tuple[str, str]:
    """Show the command and let the person run / edit / copy / cancel. Returns (RUN|CANCEL, command).
    Risky commands (see safety.py) need the word 'yes', not just Enter."""
    from .safety import warnings as check

    while True:
        warns = check(command, shell, os.getcwd())
        show_answer(term, command, explanation, warns)
        if warns:
            ans = (term.line(style("  type yes to run, e to edit, anything else cancels: ", YELLOW)) or "")
            ans = ans.strip().lower()
            k = "\n" if ans == "yes" else "e" if ans == "e" else ""
        else:
            term.write(style("  ⏎ run · e edit · c copy · esc cancel", GREY), end=" ")
            k = term.key()
            term.write()
        if k == "\n":
            return RUN, command
        if k == "e":
            new = term.line(style("  edit: ", YELLOW), prefill=command)
            if new is None or not new.strip():
                note(term, "cancelled")
                return CANCEL, command
            if new.strip() != command:
                command, explanation = new.strip(), "edited by you"
            continue
        if k == "c":
            ok = copy_to_clipboard(command)
            note(term, "copied" if ok else "couldn't find a clipboard tool", GREY if ok else RED)
            return CANCEL, command
        note(term, "cancelled")
        return CANCEL, command
