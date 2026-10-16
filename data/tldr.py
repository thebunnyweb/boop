"""tldr-pages → (description, concrete command) examples, one OS at a time.

  git clone --depth 1 https://github.com/tldr-pages/tldr data/tldr-src
  python data/tldr.py data/tldr-src --out data/out/tldr.jsonl

tldr-pages is CC BY 4.0 (© the tldr-pages team and contributors); the model card credits it.

Each page example looks like:
    - E[x]tract a (compressed) archive [f]ile into the target directory:
    `tar xf {{path/to/source.tar.ext}} {{[-C|--directory]}} {{path/to/directory}}`
Placeholders are filled with realistic values (`backup.tar.gz`, `photos`), and the values are kept so the
teacher's request can be checked to mention them. Examples with placeholders we can't fill sensibly are
dropped and counted, so the dictionary below can grow where it matters.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# tldr platform folder → (os, shell) pairs the example is valid for
PLATFORMS = {
    "common": [("macOS", "zsh"), ("Linux", "bash")],
    "osx": [("macOS", "zsh")],
    "linux": [("Linux", "bash")],
    "windows": [("Windows", "powershell")],
}

_EXAMPLE = re.compile(r"^- (.+?):?\s*\n\s*\n`(.+)`\s*$", re.M)
_PLACEHOLDER = re.compile(r"\{\{(.+?)\}\}(?!\})")
_BRACKET_LETTER = re.compile(r"\[(\w)\]")

FILE_NAMES = {
    "": ["notes", "report", "todo", "draft", "data", "config", "output", "readme"],
    ".txt": ["notes", "todo", "log", "words"], ".log": ["app", "server", "error"], ".csv": ["sales", "users"],
    ".json": ["config", "package", "data"], ".py": ["main", "app", "utils"], ".js": ["index", "app"],
    ".md": ["README", "notes"], ".html": ["index", "page"], ".jpg": ["photo", "cat"], ".png": ["logo", "screenshot"],
    ".pdf": ["invoice", "report"], ".zip": ["archive", "backup"], ".tar": ["backup", "archive"],
    ".tar.gz": ["backup", "release"], ".tar.xz": ["backup", "release"], ".tar.bz2": ["backup"], ".xz": ["backup"],
    ".tgz": ["backup"], ".7z": ["archive"], ".gz": ["backup"], ".sh": ["deploy", "build"], ".mp4": ["video", "clip"],
    ".mp3": ["song"], ".iso": ["ubuntu"], ".yml": ["docker-compose", "config"], ".yaml": ["config"],
    ".conf": ["nginx"], ".db": ["app"], ".sql": ["dump", "schema"], ".xml": ["feed"], ".c": ["main"],
    ".dmg": ["installer"], ".exe": ["setup"], ".bak": ["config"], ".patch": ["fix"], ".key": ["server"],
    ".pem": ["cert"], ".pub": ["id_ed25519"], ".docx": ["letter"], ".xlsx": ["budget"], ".wav": ["sound"],
}
DIR_NAMES = ["src", "photos", "logs", "docs", "backup", "projects", "build", "downloads", "music", "notes"]

# snake_case placeholder names → plausible values
VALUES = {
    "search_pattern": ["TODO", "error", "localhost"], "pattern": ["TODO", "error"], "regex": ["^ERROR"],
    "regular_expression": ["^ERROR"], "exact_string": ["Hello world"], "string": ["hello"], "text": ["hello"],
    "commit": ["HEAD~1", "a1b2c3d"], "branch_name": ["feature/login", "main", "dev"], "branch": ["main", "dev"],
    "remote_name": ["origin"], "remote": ["origin"], "tag_name": ["v1.0.0"], "tag": ["v1.0.0"],
    "message": ["fix typo"], "commit_message": ["fix typo"], "username": ["alice"], "user": ["alice"],
    "group": ["staff", "developers"], "groupname": ["developers"], "hostname": ["example.com"], "host": ["example.com"],
    "domain": ["example.com"], "url": ["https://example.com"], "ip_address": ["192.168.1.10"], "ip": ["192.168.1.10"],
    "port": ["3000", "8080"], "port_number": ["8080"], "pid": ["1234"], "process_id": ["1234"],
    "process_name": ["node", "python"], "name": ["node"], "program": ["node"], "command": ["ls -la"],
    "package": ["htop", "jq"], "package_name": ["htop", "jq"], "package1 package2 ...": ["htop jq"],
    "image": ["nginx"], "image_name": ["nginx"], "container_name": ["web"], "container": ["web"],
    "service": ["nginx"], "service_name": ["nginx"], "variable": ["PATH"], "env_var": ["PATH"],
    "value": ["42"], "number": ["5"], "count": ["5"], "n": ["5", "10"], "lines": ["20"], "seconds": ["5"],
    "size": ["100M"], "mode": ["644", "755"], "permissions": ["755"], "date": ["2024-01-01"],
    "volume_name": ["data"], "network_name": ["backend"], "database_name": ["shop"], "table_name": ["users"],
    "key": ["name"], "file_extension": ["txt"], "extension": ["txt"], "new_name": ["final"],
    "old_text": ["foo"], "new_text": ["bar"], "replacement": ["bar"], "old_string": ["foo"], "new_string": ["bar"],
    "email": ["alice@example.com"], "path": ["src"], "shell": ["zsh"], "interface": ["en0", "eth0"],
    "device": ["/dev/disk2"], "drive": ["C:"], "letter": ["D"], "query": ["weather"], "keyword": ["TODO"],
}


def clean_description(desc: str) -> str:
    """'E[x]tract a (compressed) archive [f]ile' → 'Extract a (compressed) archive file'"""
    desc = _BRACKET_LETTER.sub(r"\1", desc)
    desc = desc.replace("`", "").strip().rstrip(":")
    return desc[:1].upper() + desc[1:]


def parse_page(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Returns (tool name, [(description, command template), ...])."""
    m = re.match(r"#\s*(.+)", text)
    name = m.group(1).strip() if m else ""
    return name, [(clean_description(d), c.strip()) for d, c in _EXAMPLE.findall(text)]


class Filler:
    """Turns `{{path/to/file}}` and friends into concrete values, remembering what it chose."""

    def __init__(self, rng: random.Random, windows: bool = False, bsd: bool = False):
        self.rng, self.windows, self.bsd = rng, windows, bsd

    def _file(self, hint: str) -> str:
        base = re.split(r"[/\\]", hint)[-1].lower()
        if base.endswith(".ext"):                       # `source.tar.ext` → a real archive extension
            base = base[:-4] + (".gz" if base.endswith(".tar.ext") else "")
        ext = next((e for e in sorted(FILE_NAMES, key=len, reverse=True) if e and base.endswith(e)), "")
        if not ext and Path(base).suffix:
            ext = Path(base).suffix
        if not ext:
            ext = self.rng.choice([".txt", ".log", ".json", ".md", ".csv"])
        return self.rng.choice(FILE_NAMES.get(ext) or FILE_NAMES[""]) + ext

    def one(self, ph: str) -> str | None:
        ph = ph.strip()
        if ph.startswith("[") and ph.endswith("]") and "|" in ph:          # {{[-C|--directory]}}: an option spelling
            short, long = ph[1:-1].split("|", 1)
            return short if self.bsd or self.rng.random() < 0.7 else long   # BSD tools rarely take --long
        is_path = ph.startswith(("path/to/", "path\\to\\", "~/", "/path/"))
        if is_path:
            items = ph.split()
            if len(items) > 1 or "..." in ph:                                   # "file1 file2 ..." → two of them
                kind = items[0]
                return " ".join(self.one(kind) or "" for _ in range(2)).strip()
            leaf = re.split(r"[/\\]", ph)[-1]
            return self.rng.choice(DIR_NAMES) if "dir" in leaf or "folder" in leaf else self._file(leaf)
        if "|" in ph and " " not in ph:                                         # {{f|d}}: pick one
            return self.rng.choice(ph.split("|"))
        if ph in ("*.ext", "*.extension"):
            return "*" + self.rng.choice([".txt", ".log", ".py", ".jpg", ".pdf", ".md"])
        if re.search(r"\bpath\b|\.ext\b|\bpattern\b", ph):                     # `*/path/*/*.ext`: placeholder junk
            return None
        key = ph.lower()
        if key in VALUES:
            return self.rng.choice(VALUES[key])
        if re.fullmatch(r"[a-z]+(_[a-z0-9]+)+|[a-z]+", key):                    # an unknown name like `branch_id`
            return None
        return ph                                                               # already a concrete example: *.py, 10M, -1

    def fill(self, template: str) -> tuple[str, list[str]] | None:
        """(command, values that a request must mention) or None when a placeholder can't be filled."""
        values: list[str] = []
        failed: list[str] = []

        def sub(m):
            v = self.one(m.group(1))
            if v is None:
                failed.append(m.group(1))
                return m.group(0)
            if must_mention(m.group(1), v):
                values.append(v)
            return v

        cmd = _PLACEHOLDER.sub(sub, template)
        if failed:
            self.unknown = failed
            return None
        return cmd, values


def must_mention(placeholder: str, value: str) -> bool:
    """File/folder names, names from VALUES, numbers and globs belong in the request; flag spellings,
    one-letter choices and embedded commands (`{{wc -l}}`) don't."""
    ph = placeholder.strip()
    if ph.startswith("[") or value.startswith("-") and not re.fullmatch(r"-\d+", value):
        return False
    if len(value) <= 1 or (" " in value and not ph.startswith(("path", "~"))):
        return False
    return True


# GNU-only spellings that break on macOS's BSD tools: such `common` examples are kept for Linux only
GNU_ONLY = re.compile(r"--max-depth|-daystart|--wildcards|-printf\b|\bsed -i(?! '')|\bstat -c\b|"
                      r"--human-readable|--total\b|\bdu\b.*--all|--color=always|--recursive\b|"
                      r"--fixed-strings|--binary-files|--with-filename|--line-number|--context")


def iter_examples(root: Path, seed: int = 13, platforms=tuple(PLATFORMS)):
    """Yields dicts: {id, platform, os, shell, tool, description, command, values}. Also yields
    {'_unknown': [...]} for skipped examples so callers can report them."""
    rng = random.Random(seed)
    pages = root / "pages" if (root / "pages").is_dir() else root
    for plat in platforms:
        for page in sorted((pages / plat).glob("*.md")):
            tool, examples = parse_page(page.read_text(encoding="utf-8", errors="replace"))
            for i, (desc, template) in enumerate(examples):
                for os_name, shell in PLATFORMS[plat]:
                    if os_name == "macOS" and plat == "common" and GNU_ONLY.search(template):
                        continue
                    f = Filler(rng, windows=os_name == "Windows", bsd=os_name == "macOS")
                    filled = f.fill(template)
                    if filled is None:
                        yield {"_unknown": f.unknown}
                        continue
                    cmd, values = filled
                    if os_name == "macOS" and GNU_ONLY.search(cmd):
                        continue
                    yield {"id": f"{plat}/{page.stem}#{i}/{os_name}", "platform": plat, "os": os_name,
                           "shell": shell, "tool": tool, "description": desc, "command": cmd, "values": values}


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("root", help="a tldr-pages checkout (or its pages/ folder)")
    ap.add_argument("--out", default="data/out/tldr.jsonl")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--max-per-platform", default="",
                    help="e.g. common=3000,osx=1000,linux=1500,windows=800 (random sample per tldr folder)")
    a = ap.parse_args()
    caps = {k: int(v) for k, v in (x.split("=") for x in a.max_per_platform.split(",") if x)}

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kept, per_os, unknown, by_plat = 0, Counter(), Counter(), {}
    for ex in iter_examples(Path(a.root), a.seed):
        if "_unknown" in ex:
            unknown.update(ex["_unknown"])
            continue
        by_plat.setdefault(ex["platform"], []).append(ex)
    rng = random.Random(a.seed)
    with out.open("w", encoding="utf-8") as fh:
        for plat, rows in by_plat.items():
            if plat in caps and len(rows) > caps[plat]:
                rows = rng.sample(rows, caps[plat])
            for ex in rows:
                fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
                kept += 1
                per_os[ex["os"]] += 1
    print(f"kept {kept} examples {dict(per_os)} → {out}", file=sys.stderr)
    print(f"skipped {sum(unknown.values())} for unknown placeholders; most common: {unknown.most_common(25)}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
