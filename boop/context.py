"""What the model needs to know about where the command will run: OS, shell, folder, recent steps."""
from __future__ import annotations

import json
import os
import platform
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

SHELLS = ("zsh", "bash", "fish", "powershell", "cmd", "sh")
POSIX_SHELLS = ("zsh", "bash", "fish", "sh")
MAX_LISTING = 40
MAX_HISTORY = 5


def detect_os() -> str:
    if sys.platform == "darwin":
        return "macOS"
    if sys.platform.startswith("win"):
        return "Windows"
    return "Linux"


def detect_shell(explicit: str | None = None) -> str:
    """The shell integration passes --shell; otherwise guess from the environment."""
    for name in (explicit, os.environ.get("BOOP_SHELL")):
        if name:
            name = name.lower()
            if name in ("pwsh", "powershell"):
                return "powershell"
            if name in SHELLS:
                return name
    if detect_os() == "Windows":
        return "powershell"          # default on Windows; `set BOOP_SHELL=cmd` for cmd.exe
    sh = os.path.basename(os.environ.get("SHELL", "")).lower()
    return sh if sh in POSIX_SHELLS else "sh"


def listing(cwd: Path, limit: int = MAX_LISTING) -> list[str]:
    """A peek at the current folder so "this folder" / "the pdfs here" mean something."""
    try:
        entries = sorted(cwd.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    out = []
    for p in entries:
        if p.name.startswith(".") and p.name not in (".git", ".env"):
            continue
        out.append(p.name + ("/" if p.is_dir() else ""))
        if len(out) >= limit:
            out.append("…")
            break
    return out


@dataclass
class Context:
    os: str
    shell: str
    cwd: str
    files: list[str] = field(default_factory=list)
    history: list[dict] = field(default_factory=list)   # recent {"request", "command"} in this session
    piped: str | None = None                            # first lines of piped-in data, if any
    known: list = field(default_factory=list)           # [(phrase, [paths])] from the folder index

    @classmethod
    def gather(cls, shell: str | None = None, piped: str | None = None, history: list[dict] | None = None):
        cwd = Path.cwd()
        return cls(os=detect_os(), shell=detect_shell(shell), cwd=str(cwd), files=listing(cwd),
                   history=history or [], piped=piped)

    @property
    def os_detail(self) -> str:
        if self.os == "macOS":
            return f"macOS {platform.mac_ver()[0] or ''}".strip()
        return self.os


# ---- session memory (English mode) -------------------------------------------------------------

def _session_file() -> Path:
    # one file per terminal session: the shell integration sets BOOP_SESSION to the shell's PID
    sid = os.environ.get("BOOP_SESSION") or str(os.getppid())
    return Path(tempfile.gettempdir()) / f"boop-session-{sid}.json"


def load_history() -> list[dict]:
    try:
        return json.loads(_session_file().read_text())[-MAX_HISTORY:]
    except (OSError, ValueError):
        return []


def remember(request: str, command: str) -> None:
    hist = (load_history() + [{"request": request, "command": command}])[-MAX_HISTORY:]
    try:
        _session_file().write_text(json.dumps(hist))
    except OSError:
        pass


def forget() -> None:
    try:
        _session_file().unlink()
    except OSError:
        pass
