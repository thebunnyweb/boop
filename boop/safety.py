"""Flag commands that can destroy data or the machine. Rules, not the model: this must never miss.

Over-warning is fine (you type `yes`); under-warning is not.
"""
from __future__ import annotations

import re
from pathlib import Path

# (pattern, warning) — checked against the command with quoted strings blanked out where noted
_POSIX = [
    (r"\brm\s+(-\w*[rR]|--recursive)", "deletes folders and everything in them (rm -r)"),
    (r"\b(rm|unlink)\b", "deletes files"),
    (r"\bfind\b.*(-delete|-exec\s+rm)", "deletes the files it finds"),
    (r"\bxargs\b.*\brm\b", "deletes files passed through xargs"),
    (r"\bshred\b|\bsrm\b", "irreversibly destroys files"),
    (r"\bdd\b.*\bof=", "writes raw data to a file or disk (dd)"),
    (r"\bmkfs(\.\w+)?\b|\bdiskutil\s+(erase|partition|zero)", "formats a disk"),
    (r"\bsudo\b|\bdoas\b", "runs as administrator (sudo)"),
    (r"\bchmod\s+-\w*R|\bchown\s+-\w*R", "changes permissions recursively"),
    (r"\bgit\s+push\b.*(--force\b|--force-with-lease\b|\s-f\b)", "force-pushes and can overwrite remote history"),
    (r"\bgit\s+reset\b.*--hard", "throws away uncommitted changes (git reset --hard)"),
    (r"\bgit\s+clean\b.*-\w*f", "deletes untracked files (git clean)"),
    (r"\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)", "discards uncommitted changes"),
    (r"\bgit\s+(checkout\s+--\s+\S|restore\s+(?!--staged\b)(--worktree\s+)?[^-\s])",
     "discards your uncommitted edits to those files"),
    (r"\bgit\s+branch\s+-D\b", "force-deletes a branch"),
    (r"\bgit\s+stash\s+(drop|clear)\b", "deletes stashed changes"),
    (r"\b(kill|killall|pkill)\b|\bfuser\b.*\s-k\b", "kills processes"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "shuts down or restarts the machine"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba|z|da)?sh\b", "runs a script downloaded from the internet"),
    (r":\(\)\s*\{", "fork bomb"),
    (r"\btruncate\b", "truncates a file"),
    (r"\bmv\b.*\s/dev/null\b", "moves files into /dev/null (deletes them)"),
]
_WINDOWS = [
    (r"\b(Remove-Item|ri|rm|rmdir|rd|del|erase)\b", "deletes files"),
    (r"\bformat\s+[a-z]:|\bFormat-Volume\b|\bdiskpart\b|\bClear-Disk\b", "formats a disk"),
    (r"\b(Stop-Process|taskkill)\b", "kills processes"),
    (r"\b(Restart-Computer|Stop-Computer|shutdown)\b", "shuts down or restarts the machine"),
    (r"\bSet-ExecutionPolicy\b", "changes the script execution policy"),
    (r"\breg\s+delete\b|\bRemove-ItemProperty\b", "deletes registry entries"),
    (r"\bClear-Content\b|\bSet-Content\b", "overwrites file contents"),
    (r"\b(iex|Invoke-Expression)\b", "runs code from a string (often downloaded)"),
    (r"\bStart-Process\b.*-Verb\s+RunAs", "runs as administrator"),
    (r"\bgit\s+push\b.*(--force\b|\s-f\b)", "force-pushes and can overwrite remote history"),
    (r"\bgit\s+reset\b.*--hard", "throws away uncommitted changes (git reset --hard)"),
    (r"\bgit\s+clean\b.*-\w*f", "deletes untracked files (git clean)"),
]
# `> file` overwrites; `>>`, `2>`, `&>`, `>&2`, `>/dev/null` and `2>&1` don't count
_OVERWRITE = re.compile(r"(?<![>&0-9-])>(?![>&])(?!\s*(/dev/null|\$null)\b)\s*(\S*)")
_QUOTED = re.compile(r"'[^']*'|\"(?:\\.|[^\"\\])*\"")


def _unquote(cmd: str) -> str:
    return _QUOTED.sub("''", cmd)


def _overwrites_existing(bare: str, cwd: str | None) -> bool:
    """`> file` only matters if the file is already there. Unknown folder or unclear target: assume it is."""
    for m in _OVERWRITE.finditer(bare):
        target = m.group(2).strip()
        if not target:
            continue                                   # `cmd >` with nothing after it isn't a redirect
        if cwd is None or target in ("''", '""') or any(c in target for c in "$*?`~"):
            return True
        if (Path(cwd) / target).exists():
            return True
    return False


def warnings(command: str, shell: str, cwd: str | None = None) -> list[str]:
    """Plain-English reasons this command is risky; empty list when it's fine to run with Enter.
    With `cwd`, a `> file` redirect only counts when that file already exists."""
    if not command.strip():
        return []
    rules = _WINDOWS if shell in ("powershell", "cmd") else _POSIX
    bare = _unquote(command)
    found = []
    for pat, why in rules:
        if re.search(pat, bare, re.I if shell in ("powershell", "cmd") else 0) and why not in found:
            found.append(why)
    if shell != "cmd" and _overwrites_existing(bare, cwd):
        found.append("overwrites a file (>)")
    if shell == "powershell" and re.search(r"\bOut-File\b(?!.*-Append)", bare, re.I):
        found.append("overwrites a file (Out-File)")
    return found


def is_dangerous(command: str, shell: str, cwd: str | None = None) -> bool:
    return bool(warnings(command, shell, cwd))
