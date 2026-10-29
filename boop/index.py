"""Where your folders are, so "my anime folder" can become ~/Media/Anime.

  python -m boop.index --build      (boop runs this itself in the background, at most once a day)

Only folder names are indexed, never file names or contents, and only under your home folder (or
BOOP_INDEX_ROOTS, separated by os.pathsep). Hidden folders, caches, build output and system folders are
skipped. The index is a plain text file in boop's state folder and never leaves the machine.

At request time, `lookup()` finds the words that name a folder ("from my anime folder", "in invoices"),
matches them against the index, and the prompt gets one line: `Known folders: anime → ~/Media/Anime`.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MAX_DEPTH = 6
MAX_FOLDERS = 300_000
FRESH_FOR = 24 * 3600
MAX_PER_NAME = 3

SKIP = {
    "node_modules", "__pycache__", "venv", ".venv", "env", "site-packages", "dist", "build", "target", "out",
    ".git", ".hg", ".svn", ".cache", "cache", "caches", "tmp", "temp", ".tox", ".mypy_cache", ".gradle",
    ".npm", ".cargo", ".rustup", "go", "pkg", "vendor", "bower_components", "deriveddata",
    "library", "applications", ".trash", "appdata", "application data", "snap", "flatpak", "proc",
}

# a folder name usually sits after one of these, or right before "folder"
_PREPS = {"in", "from", "inside", "under", "into", "to", "of", "at", "within", "onto"}
_OWNERS = {"my", "the", "our", "this", "that", "your", "his", "her", "their"}
_NOUNS = {"folder", "folders", "directory", "directories", "dir", "dirs"}
_STOP = {"here", "this", "it", "them", "there", "all", "files", "file", "current", "same", "new", "a", "an",
         "home", "everything", "every", "each", "one", "subfolders", "subfolder", "every", "any", "some",
         "and", "or", "with", "named", "called", "than", "over", "under", "about", "last", "first", "biggest",
         "largest", "smallest", "newest", "oldest", "empty", "other", "these", "those"}
_VERBS = {"zip", "unzip", "delete", "remove", "open", "copy", "move", "show", "list", "find", "get", "count",
          "make", "create", "rename", "compress", "extract", "clean", "sort", "search", "grep", "go", "cd",
          "back", "up", "sync", "check", "print", "display", "tar", "merge"}
_BREAK = _PREPS | _OWNERS | _STOP | _VERBS | _NOUNS


def state_dir() -> Path:
    if sys.platform.startswith("win"):
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    else:
        base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "boop"


def index_file() -> Path:
    return state_dir() / "folders.txt"


def roots() -> list[Path]:
    env = os.environ.get("BOOP_INDEX_ROOTS")
    return [Path(p).expanduser() for p in env.split(os.pathsep) if p] if env else [Path.home()]


# ---- building ---------------------------------------------------------------------------------

def walk(root: Path, max_depth: int = MAX_DEPTH, limit: int = MAX_FOLDERS,
         denied: list | None = None) -> list[str]:
    """Every folder under root (breadth first, so shallow folders survive the limit). Folders the OS won't let
    us read go into `denied` (on macOS: Desktop, Documents, Downloads until the terminal is allowed in)."""
    out, level = [], [str(root)]
    for depth in range(max_depth):
        nxt = []
        for d in level:
            try:
                with os.scandir(d) as it:
                    for e in it:
                        name = e.name
                        if name.startswith(".") or name.lower() in SKIP:
                            continue
                        try:
                            if not e.is_dir(follow_symlinks=False):
                                continue
                        except OSError:
                            continue
                        out.append(e.path)
                        nxt.append(e.path)
                        if len(out) >= limit:
                            return out
            except PermissionError:
                if denied is not None and depth <= 1:
                    denied.append(d)
                continue
            except OSError:
                continue
        level = nxt
        if not level:
            break
    return out


DENIED: list[str] = []          # from the last build(): folders the OS refused to list


def build() -> int:
    DENIED.clear()
    folders = [p for r in roots() for p in walk(r, denied=DENIED)]
    index_file().parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=index_file().parent, prefix=".folders.")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(folders) + "\n")
    os.replace(tmp, index_file())             # readers never see a half-written index
    return len(folders)


def stale() -> bool:
    try:
        return time.time() - index_file().stat().st_mtime > FRESH_FOR
    except OSError:
        return True


def refresh_in_background() -> None:
    """Start a detached rebuild if the index is old; boop itself never waits for it."""
    if os.environ.get("BOOP_NO_INDEX") or not stale():
        return
    try:
        index_file().parent.mkdir(parents=True, exist_ok=True)
        index_file().touch()                  # so a second boop in the next minute doesn't start another
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if sys.platform.startswith("win"):
            kw["creationflags"] = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        else:
            kw["start_new_session"] = True
        subprocess.Popen([sys.executable, "-m", "boop.index", "--build"], **kw)
    except OSError:
        pass


# ---- looking up -----------------------------------------------------------------------------------

def norm(name: str) -> str:
    """'My_Anime-Stuff' → 'my anime stuff'; a trailing plural s is dropped so 'invoice' finds 'Invoices'."""
    words = re.sub(r"[\s_\-.]+", " ", name.lower()).strip().split()
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words)


def load(path: Path | None = None) -> dict[str, list[str]]:
    """normalised folder name → paths, shallowest first."""
    try:
        lines = (path or index_file()).read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    by_name: dict[str, list[str]] = {}
    for p in lines:
        if p:
            by_name.setdefault(norm(os.path.basename(p)), []).append(p)
    for paths in by_name.values():
        paths.sort(key=lambda p: (p.count(os.sep), len(p)))
    return by_name


def mentions(request: str) -> list[str]:
    """The phrases in a request that name a folder: 'pdfs from my anime folder' → 'anime',
    'in my tax stuff folder' → 'tax stuff', 'in downloads' → 'downloads'. Paths and file names
    (anything with / . ~) are left alone: the model can use those as written."""
    toks = re.findall(r"[\w.\-~/\\:']+", request.lower())
    plain = lambda t: not re.search(r"[./~\\:]", t) and not t.isdigit()
    found = []

    def add(words):
        phrase = " ".join(words)
        if phrase and phrase not in found:
            found.append(phrase)

    for i, t in enumerate(toks):
        if t in _NOUNS:                                   # up to three words right before "folder"
            words, j = [], i - 1
            while j >= 0 and len(words) < 3 and toks[j] not in _BREAK and plain(toks[j]):
                words.insert(0, toks[j])
                j -= 1
            add(words)
        elif t in _PREPS:                                 # "in downloads", "from my Movies"
            j = i + 1
            while j < len(toks) and toks[j] in _OWNERS:
                j += 1
            k = j                                         # "in my tax stuff folder": the noun rule has it
            while k < len(toks) and k - j < 4 and toks[k] not in _BREAK:
                k += 1
            if k < len(toks) and toks[k] in _NOUNS:
                continue
            if j < len(toks) and toks[j] not in _BREAK and plain(toks[j]):
                add([toks[j]])
    return found


def lookup(request: str, *, local: list[str] | None = None, index: dict | None = None,
           home: str | None = None) -> list[tuple[str, list[str]]]:
    """[(phrase, [paths…])] for folder names in the request that the index knows. A folder in the current
    listing wins: "in src" means ./src, so it's left to the model."""
    if index is None and os.environ.get("BOOP_NO_INDEX"):
        return []
    by_name = load() if index is None else index
    if not by_name:
        return []
    here = {norm(f.rstrip("/\\")) for f in (local or [])}
    out = []
    for phrase in mentions(request):
        key = norm(phrase)
        words = key.split()
        # "my old anime folder" may be indexed as "old anime" or just "anime": try the longest tail first
        for i in range(len(words)):
            k = " ".join(words[i:])
            if k in here:
                break
            if k in by_name:
                out.append((phrase, [display(p, home) for p in by_name[k][:MAX_PER_NAME]]))
                break
    return out


def denied_hint() -> str:
    """What to do when the OS hid folders from the index (empty when nothing was hidden)."""
    if not DENIED:
        return ""
    names = ", ".join(display(d) for d in DENIED[:6])
    if sys.platform == "darwin":
        how = ("macOS privacy settings blocked them: System Settings → Privacy & Security → Files and Folders "
               "(or Full Disk Access) → allow your terminal app, then run boop --reindex")
    else:
        how = "no permission to read them; fix the permissions, then run boop --reindex"
    return f"couldn't look inside {names}: {how}.\n"


def display(path: str, home: str | None = None) -> str:
    """~/Media/Anime on macOS/Linux (short, works in every POSIX shell). With a space it's
    $HOME/Tax Stuff instead, because "~/Tax Stuff" in quotes is not expanded. Full paths on Windows."""
    if sys.platform.startswith("win"):
        return path
    home = home or str(Path.home())
    if not (path == home or path.startswith(home + os.sep)):
        return path
    return ("$HOME" if " " in path else "~") + path[len(home):]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--lookup", metavar="REQUEST")
    a = ap.parse_args()
    if a.build:
        t0 = time.time()
        n = build()
        print(f"indexed {n} folders in {time.time() - t0:.1f}s → {index_file()}")
        print(denied_hint() or "", end="")
    if a.lookup:
        for phrase, paths in lookup(a.lookup):
            print(f"{phrase} → {', '.join(paths)}")


if __name__ == "__main__":
    main()
