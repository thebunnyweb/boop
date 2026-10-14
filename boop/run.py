"""Run a command in the user's shell dialect (used when boop runs it itself: pipe mode, no shell integration)."""
from __future__ import annotations

import shutil
import subprocess
from typing import IO


def argv_for(shell: str, command: str) -> list[str]:
    if shell == "powershell":
        exe = shutil.which("pwsh") or shutil.which("powershell") or "powershell"
        return [exe, "-NoProfile", "-NonInteractive", "-Command", command]
    if shell == "cmd":
        return ["cmd", "/d", "/s", "/c", command]
    exe = shutil.which(shell) or shutil.which("sh") or "/bin/sh"
    return [exe, "-c", command]


def run(command: str, shell: str, stdin: IO[bytes] | None = None) -> int:
    """Runs and streams output straight to the terminal / the next command in the pipe. Returns the exit code."""
    try:
        return subprocess.call(argv_for(shell, command), stdin=stdin)
    except KeyboardInterrupt:
        return 130
    except OSError as e:
        print(f"boop: couldn't start {shell}: {e}")
        return 127
