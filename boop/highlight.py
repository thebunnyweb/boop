"""Colour a shell command the way an editor would: program, flags, paths, strings, operators.

Returns prompt_toolkit-style (class, text) pairs; the panel and the line left after a run share them.
It's a display aid only — safety checks never look at it.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"""
    (?P<space>\s+)
  | (?P<string>'[^']*'?|"(?:\\.|[^"\\])*"?)
  | (?P<op>\|\||&&|2>&1|[0-9]?>>|[0-9]?>|<|\||;|&)
  | (?P<word>[^\s'"|;&<>]+)
""", re.X)

_PATHISH = re.compile(r"^(~|\.{1,2}(/|$)|/|\$HOME|[A-Za-z]:\\)|/")


def tokens(command: str) -> list[tuple[str, str]]:
    out, starts_segment = [], True
    for m in _TOKEN.finditer(command):
        kind, text = m.lastgroup, m.group()
        if kind == "space":
            out.append(("", text))
            continue
        if kind == "op":
            out.append(("class:sh.op", text))
            starts_segment = text not in (">", ">>", "2>&1", "<") and not text.endswith(">")
            continue
        if kind == "string":
            out.append(("class:sh.string", text))
        elif starts_segment and kind == "word" and not text.startswith("-"):
            if "=" in text and not text.startswith("="):          # FOO=bar cmd
                out.append(("class:sh.var", text))
                continue
            out.append(("class:sh.cmd", text))
        elif text.startswith("-"):
            out.append(("class:sh.flag", text))
        elif text.startswith("$"):
            out.append(("class:sh.var", text))
        elif _PATHISH.search(text):
            out.append(("class:sh.path", text))
        elif text.isdigit() or re.fullmatch(r"[+-]?\d+[kKmMgG]?", text):
            out.append(("class:sh.num", text))
        else:
            out.append(("class:sh.arg", text))
        starts_segment = False
    return out
