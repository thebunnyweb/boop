"""Talking to the person at the keyboard. Always via the terminal device, never stdin/stdout, so boop still
works when stdin is a pipe and when the shell captures stdout."""
from __future__ import annotations

import os
import subprocess
import sys

IS_WINDOWS = sys.platform.startswith("win")


def _color_ok() -> bool:
    return not os.environ.get("NO_COLOR") and os.environ.get("TERM") != "dumb"


if IS_WINDOWS:
    os.system("")   # turns on ANSI escape handling in the Windows console


def style(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _color_ok() else text


DIM, BOLD, RED, YELLOW, CYAN, GREEN = "2", "1", "31", "33", "36", "32"


class Terminal:
    """Reads keys and lines from the real terminal (/dev/tty or the Windows console)."""

    def __init__(self):
        if IS_WINDOWS:
            self.out = open("CONOUT$", "w", encoding="utf-8", errors="replace")
            self.inp = open("CONIN$", "r", encoding="utf-8", errors="replace")
        else:
            self.out = open("/dev/tty", "w")
            self.inp = open("/dev/tty", "r")

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
        """A line of input, optionally pre-filled for editing. None on Ctrl-C/Ctrl-D."""
        self.out.write(prompt)
        self.out.flush()
        if prefill and not IS_WINDOWS:
            try:
                return _readline_prefilled(self, prompt, prefill)
            except Exception:       # no readline: fall back to plain input
                pass
        if prefill:
            self.write(style(f"(current: {prefill})", DIM))
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


def _readline_prefilled(term: Terminal, prompt: str, prefill: str) -> str | None:
    """GNU readline with the text already typed in, reading from the terminal even if stdin is a pipe."""
    import readline

    # input() talks to fds 0/1; point both at the terminal so a pipe or captured stdout isn't touched
    sys.stdout.flush()
    saved = os.dup(0), os.dup(1)
    os.dup2(term.inp.fileno(), 0)
    os.dup2(term.out.fileno(), 1)
    readline.set_startup_hook(lambda: readline.insert_text(prefill))
    try:
        term.out.write("\r")            # input() prints the prompt itself
        term.out.flush()
        return input(prompt)
    except (EOFError, KeyboardInterrupt):
        return None
    finally:
        readline.set_startup_hook()
        sys.stdout.flush()
        os.dup2(saved[0], 0)
        os.dup2(saved[1], 1)
        for fd in saved:
            os.close(fd)


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


def confirm(term: Terminal, command: str, explanation: str, shell: str) -> tuple[str, str]:
    """Show the command and let the person run / edit / copy / explain / quit. Returns (RUN|CANCEL, command).
    Risky commands (see safety.py) need the word 'yes', not just Enter."""
    from .safety import warnings as check

    warns = check(command, shell, os.getcwd())
    while True:
        term.write("  " + style(command, BOLD + ";" + CYAN))
        for w in warns:
            term.write("  " + style(f"⚠ {w}", RED))
        if warns:
            ans = term.line(style("  type 'yes' to run, e to edit, anything else to cancel: ", YELLOW))
            if ans is None:
                return CANCEL, command
            ans = ans.strip().lower()
            if ans == "yes":
                return RUN, command
            if ans != "e":
                return CANCEL, command
            k = "e"
        else:
            term.write(style("  [⏎] run  [e] edit  [c] copy  [x] explain  [q] quit", DIM), end=" ")
            k = term.key()
            term.write()
        if k == "\n":
            return RUN, command
        if k == "e":
            new = term.line(style("  edit› ", YELLOW), prefill=command)
            if new is None or not new.strip():
                return CANCEL, command
            command, explanation = new.strip(), ("(edited)" if new.strip() != command else explanation)
            warns = check(command, shell, os.getcwd())
            continue
        if k == "c":
            ok = copy_to_clipboard(command)
            term.write(style("  copied." if ok else "  couldn't find a clipboard tool.", GREEN if ok else RED))
            return CANCEL, command
        if k == "x":
            term.write("  " + style(explanation or "(no explanation)", DIM))
            continue
        return CANCEL, command
