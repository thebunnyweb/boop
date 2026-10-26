"""The panel: an fzf-style picker that opens under your prompt and goes away when you're done.

  ╭─ boop ─────────────────────────────────────── ~/code/site · zsh ─╮
  │ ❯ find pdfs over 10mb in downloads                               │
  │   ✦ boop · 0.8s ─────────────────────────────────────────────────│
  │ ▌ find ~/Downloads -type f -name '*.pdf' -size +10M              │
  │   find . -name '*.pdf'                       list pdfs here      │
  │                                                                  │
  │ ──────────────────────────────────────────────────────────────── │
  │ Finds PDF files larger than 10 MB in Downloads.                  │
  ╰─ ⏎ run  ⇥ edit  ^Y copy  esc quit ───────────────────────────────╯

Typing filters what you ran before (fuzzy, instant, no model). Enter on the first row asks the model.
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .highlight import tokens
from .ui import IS_WINDOWS, Terminal, home_path, text_width

SPIN = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
HEIGHT = 13                 # rows, borders included
MAX_WIDTH = 100             # columns; a wide terminal gets a panel, not a banner
MAX_HISTORY = 500

# Catppuccin Mocha-ish: soft pastels on a dark terminal. prompt_toolkit maps these down to 256 or 16 colours
# when the terminal can't do true colour (Apple Terminal), so they stay readable everywhere.
THEME = {
    "border": "#585b70", "rule": "#45475a", "title": "#fab387 bold", "dim": "#7f849c", "text": "#cdd6f4",
    "accent": "#cba6f7", "ok": "#a6e3a1", "pointer": "#cba6f7 bold", "prompt": "#cba6f7 bold",
    "row.on": "bg:#313244", "hit": "#fab387 bold underline", "warn": "#f38ba8", "warnicon": "#f38ba8 bold",
    "pill": "bg:#45475a #cdd6f4 bold", "keylabel": "#7f849c", "ask": "#cba6f7 bold", "quote": "#bac2de italic",
    "sh.cmd": "#89b4fa bold", "sh.flag": "#f9e2af", "sh.string": "#a6e3a1", "sh.path": "#94e2d5",
    "sh.op": "#f38ba8 bold", "sh.var": "#eba0ac", "sh.num": "#fab387", "sh.arg": "#cdd6f4",
}


# ---- what you ran before ----------------------------------------------------------------------

def history_file() -> Path:
    base = os.environ.get("XDG_STATE_HOME") or (os.environ.get("LOCALAPPDATA") if IS_WINDOWS else None) \
        or str(Path.home() / ".local" / "state")
    return Path(base) / "boop" / "history.jsonl"


def load_runs() -> list[dict]:
    """Newest first, one row per command."""
    try:
        lines = history_file().read_text(encoding="utf-8").splitlines()[-MAX_HISTORY:]
    except OSError:
        return []
    seen, out = set(), []
    for line in reversed(lines):
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("command") and r["command"] not in seen:
            seen.add(r["command"])
            out.append(r)
    return out


def record_run(request: str, command: str) -> None:
    try:
        history_file().parent.mkdir(parents=True, exist_ok=True)
        with history_file().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"request": request, "command": command, "cwd": os.getcwd(),
                                 "ts": int(time.time())}) + "\n")
    except OSError:
        pass


def fuzzy(query: str, text: str) -> tuple[int, list[int]] | None:
    """fzf-ish: every query character in order. Higher score for runs and word starts; None if no match."""
    q, t = query.lower().replace(" ", ""), text.lower()
    if not q:
        return 0, []
    pos, score, last = [], 0, -2
    i = 0
    for ch in q:
        i = t.find(ch, i)
        if i < 0:
            return None
        score += 3 if i == last + 1 else 1
        score += 2 if i == 0 or not t[i - 1].isalnum() else 0
        pos.append(i)
        last, i = i, i + 1
    return score - (pos[-1] - pos[0]) // 4, pos


# ---- the panel ----------------------------------------------------------------------------------

@dataclass
class Item:
    kind: str                       # "ask" | "answer" | "past"
    command: str = ""
    request: str = ""
    explanation: str = ""
    hits: list[int] = field(default_factory=list)   # matched positions in `request`


@dataclass
class Result:
    action: str                     # "run" | "copy"
    command: str
    request: str


def pick(term: Terminal, *, ask, warnings_for, cwd: str, shell: str, model: str,
         query: str = "", auto_ask: bool = False, preload=None) -> Result | None:
    """Open the panel. `ask(request) -> Answer`, `warnings_for(command) -> [str]`. None means esc/Ctrl-D."""
    from prompt_toolkit.application import Application, get_app
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.input import create_input
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, VSplit, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.output import create_output
    from prompt_toolkit.styles import Style

    runs = load_runs()
    st = dict(items=[], sel=0, mode="search", busy=None, error="", answer=None, took=None, asked_for=None)
    #  mode: search (typing a request) | edit (changing a command) | confirm (risky: type yes)

    if preload:
        threading.Thread(target=preload, daemon=True).start()     # load the model while you type

    def width() -> int:
        try:
            return min(MAX_WIDTH, get_app().output.get_size().columns)
        except Exception:
            return 80

    def refresh_items() -> None:
        q = buf.text.strip()
        items = []
        if st["answer"] is not None and st["asked_for"] == q:
            items.append(st["answer"])
            items += st.get("alts", [])
        elif q:
            items.append(Item("ask", request=q))
        scored = []
        for r in runs:
            m = fuzzy(q, r.get("request", "")) if q else (0, [])
            m2 = fuzzy(q, r["command"]) if q and m is None else None
            if m is None and m2 is None:
                continue
            score, hits = m if m is not None else (m2[0] - 1, [])
            scored.append((-score, len(scored), Item("past", r["command"], r.get("request", ""), hits=hits)))
        items += [it for *_, it in sorted(scored)[:40]] if q else [it for *_, it in scored[:40]]
        st["items"], st["sel"] = items, 0

    def selected() -> Item | None:
        return st["items"][st["sel"]] if st["items"] else None

    def start_ask() -> None:
        q = buf.text.strip()
        if not q or st["busy"]:
            return
        st["busy"], st["error"] = time.monotonic(), ""

        def work():
            try:
                ans = ask(q)
                if ans.command:
                    st["answer"] = Item("answer", ans.command, q, ans.explanation)
                    st["alts"] = [Item("answer", c, q, note) for c, note in getattr(ans, "alternatives", [])]
                    st["asked_for"] = q
                else:
                    st["error"] = ans.explanation or "no command for that, try rewording it"
            except Exception as e:                      # keep the panel alive whatever the model does
                st["error"] = f"{type(e).__name__}: {e}"
            st["took"] = time.monotonic() - st["busy"]
            st["busy"] = None
            if buf.text.strip() == q:
                refresh_items()
            app.invalidate()

        threading.Thread(target=work, daemon=True).start()

    def choose(it: Item, app_) -> None:
        if it.kind == "ask" and it.request.lower() in ("exit", "quit", "q", ":q"):
            app_.exit(result=None)
            return
        if it.kind == "ask":
            start_ask()
            return
        if warnings_for(it.command):
            st["mode"], st["pending"] = "confirm", it
            buf.document = Document("", 0)
            return
        app_.exit(result=Result("run", it.command, it.request or buf.text.strip()))

    buf = Buffer(document=Document(query, len(query)), multiline=False,
                 on_text_changed=lambda _: refresh_items() if st["mode"] == "search" else None)
    keys = KeyBindings()

    @keys.add("enter")
    def _(e):
        if st["mode"] == "confirm":
            it = st["pending"]
            if buf.text.strip().lower() == "yes":
                e.app.exit(result=Result("run", it.command, it.request))
            else:
                back_to_search()
            return
        if st["mode"] == "edit":
            cmd = buf.text.strip()
            if cmd:
                it = Item("answer", cmd, st["edit_of"].request, "edited by you")
                st["mode"] = "search"
                choose(it, e.app)
            return
        it = selected()
        if it:
            choose(it, e.app)

    @keys.add("tab")
    def _(e):
        it = selected()
        if st["mode"] == "search" and it and it.kind != "ask":
            st["mode"], st["edit_of"] = "edit", it
            buf.document = Document(it.command, len(it.command))

    @keys.add("c-y")
    def _(e):
        it = selected()
        if it and it.kind != "ask" and st["mode"] == "search":
            e.app.exit(result=Result("copy", it.command, it.request))

    for k in ("up", "c-p", "c-k"):
        @keys.add(k)
        def _(e):
            if st["items"]:
                st["sel"] = max(0, st["sel"] - 1)

    for k in ("down", "c-n", "c-j"):
        @keys.add(k)
        def _(e):
            if st["items"]:
                st["sel"] = min(len(st["items"]) - 1, st["sel"] + 1)

    def back_to_search():
        q = st.get("asked_for") or ""
        st["mode"] = "search"
        buf.document = Document(q, len(q))
        refresh_items()

    @keys.add("escape", eager=True)
    def _(e):
        if st["mode"] != "search":
            back_to_search()
        else:
            e.app.exit(result=None)

    @keys.add("c-c")
    @keys.add("c-d")
    def _(e):
        if st["mode"] != "search":
            back_to_search()
        elif buf.text:
            buf.reset()
        else:
            e.app.exit(result=None)

    # ---- drawing: every row is `│ content │`, content padded/cut to the inner width -----------------

    def fit(frags, w):
        out, used = [], 0
        for style_, text in frags:
            for ch in text:
                cw = text_width(ch)
                if used + cw > w:
                    if out and used < w + 1:
                        out[-1] = (out[-1][0], out[-1][1][:-1] + "…") if out[-1][1] else out[-1]
                    return out + [("", " " * max(0, w - used))]
                out.append((style_, ch))
                used += cw
        return out + [("", " " * (w - used))]

    def row(fn, bg=lambda: ""):
        def render():
            w, b = width() - 4, bg()
            body = [(f"{s} {b}".strip(), t) for s, t in fit(fn(w), w)] if b else fit(fn(w), w)
            return [("class:border", "│ ")] + body + [("class:border", " │")]
        return Window(FormattedTextControl(render), height=1, dont_extend_height=True)

    def top():
        w = width()
        left = [("class:border", "╭─ "), ("class:title", "boop"), ("class:border", " ")]
        right_text = f" {_short_path(home_path(cwd), min(w - 22, w // 2))} · {shell} "
        right = [("class:dim", right_text), ("class:border", "─╮")]
        fill = w - 8 - text_width(right_text) - 2
        return left + [("class:border", "─" * max(0, fill))] + right

    def bottom():
        w = width()
        if st["mode"] == "confirm":
            keys_ = [("type yes ⏎", "run"), ("esc", "back")]
        elif st["mode"] == "edit":
            keys_ = [("⏎", "run"), ("esc", "back")]
        else:
            keys_ = [("⏎", "run" if (selected() and selected().kind != "ask") else "ask"), ("⇥", "edit"),
                     ("^Y", "copy"), ("esc", "quit")]
        frags, used = [("class:border", "╰─ ")], 3
        for k, label in keys_:
            piece = f" {k}  {label}  "
            if used + text_width(piece) > w - 3:
                break
            frags += [("class:pill", f" {k} "), ("class:keylabel", f" {label}  ")]
            used += text_width(piece)
        return frags + [("class:border", "─" * max(0, w - used - 1) + "╯")]

    def info(w):
        if st["mode"] == "confirm":
            ws = warnings_for(st["pending"].command)
            return [("class:warnicon", "⚠ "), ("class:warn", "; ".join(ws)), ("class:dim", " · type yes to run it")]
        if st["mode"] == "edit":
            return [("class:accent", "✎ "), ("class:dim", "editing · ⏎ runs your version")]
        if st["busy"]:
            ch = SPIN[int(time.monotonic() * 12) % len(SPIN)]
            secs = time.monotonic() - st["busy"]
            return [("class:accent", f"{ch} "), ("class:dim", "asking "), ("class:text", model),
                    ("class:dim", f" · {secs:.1f}s ")]
        if st["error"]:
            return [("class:warn", st["error"])]
        n_past = sum(1 for it in st["items"] if it.kind == "past")
        head = []
        if st["answer"] is not None and st["asked_for"] == buf.text.strip():
            head = [("class:ok", "✦ "), ("class:dim", f"boop · {st['took']:.1f}s  ")]
        elif not buf.text.strip():
            head = [("class:dim", "type what you want · ↑↓ recent  " if runs else "type what you want  ")]
        tail = f"{n_past} past " if n_past else ""
        used = sum(text_width(t) for _, t in head) + len(tail)
        return head + [("class:dim", tail), ("class:rule", "─" * max(0, w - used))]

    def row_index(i):
        return max(0, st["sel"] - (LIST - 1)) + i

    def list_row(i):
        def fn(w):
            items = st["items"]
            j = row_index(i)
            if j >= len(items):
                return []
            it, on = items[j], j == st["sel"]
            mark = [("class:pointer", "▌ ")] if on else [("", "  ")]
            if it.kind == "ask":
                return mark + [("class:ask", "✦ ask boop  "), ("class:quote", f"“{it.request}”")]
            cmd = tokens(it.command)
            frags = mark + ([(f"{s} bold", t) for s, t in cmd] if on else cmd)
            if it.kind == "past" and it.request:
                room = w - 2 - text_width(it.command) - 4
                if room > 8:
                    req = it.request if text_width(it.request) <= room else it.request[:room - 1] + "…"
                    req_frags = [("class:hit" if k in it.hits else "class:dim", ch) for k, ch in enumerate(req)]
                    frags += [("", " " * (room - text_width(req) + 4))] + req_frags
            elif it.explanation.startswith("same, in "):
                frags += [("class:dim", "   " + it.explanation)]
            return frags
        return row(fn, bg=lambda: "class:row.on" if row_index(i) == st["sel"] and st["items"] else "")

    def preview(line_no):
        def fn(w):
            if st["mode"] in ("edit", "confirm"):
                it = st.get("pending") if st["mode"] == "confirm" else None
                text = it.explanation if it else ""
            else:
                it = selected()
                text = "" if not it or it.kind == "ask" else (it.explanation or
                                                              (f"you ran this for “{it.request}”" if it.request else ""))
            ws = warnings_for(it.command) if it and it.kind != "ask" and st["mode"] == "search" else []
            lines = [[("class:warnicon", "⚠ "), ("class:warn", w_)] for w_ in ws] + \
                [[("class:text", l)] for l in _wrap(text, w)]
            return lines[line_no] if line_no < len(lines) else []
        return row(fn)

    def prompt_frags():
        sign = {"search": "❯ ", "edit": "✎ ", "confirm": "yes? "}[st["mode"]]
        return [("class:border", "│ "), ("class:prompt", sign)]

    LIST = HEIGHT - 7
    rows = [Window(FormattedTextControl(top), height=1),
            VSplit([Window(FormattedTextControl(prompt_frags), dont_extend_width=True, height=1),
                    Window(BufferControl(buf), height=1),
                    Window(FormattedTextControl([("class:border", " │")]), width=2, height=1)]),
            row(info)]
    rows += [list_row(i) for i in range(LIST)]
    rows += [row(lambda w: [("class:rule", "─" * w)]), preview(0), preview(1),
             Window(FormattedTextControl(bottom), height=1)]

    from prompt_toolkit.layout.dimension import Dimension
    panel = VSplit([HSplit(rows, width=lambda: Dimension.exact(width())), Window()])
    app = Application(
        layout=Layout(panel, focused_element=buf), key_bindings=keys, full_screen=False,
        erase_when_done=True, refresh_interval=0.1, mouse_support=False,
        input=create_input(stdin=term.inp), output=create_output(stdout=term.out),
        style=Style.from_dict(THEME))
    refresh_items()
    if auto_ask and query.strip():
        app.pre_run_callables.append(start_ask)
    return app.run()


def _short_path(path: str, room: int) -> str:
    """~/code/projects/site/api → …/site/api when it doesn't fit."""
    if text_width(path) <= room:
        return path
    parts = path.split(os.sep)
    while len(parts) > 1 and text_width("…" + os.sep + os.sep.join(parts)) > room:
        parts.pop(0)
    short = "…" + os.sep + os.sep.join(parts)
    return short if text_width(short) <= room else "…" + path[-(room - 1):]


def _wrap(text: str, w: int) -> list[str]:
    out, line = [], ""
    for word in (text or "").split():
        if line and text_width(line) + 1 + text_width(word) > w:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    return out + ([line] if line else [])
