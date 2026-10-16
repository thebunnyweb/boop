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

# path/to/<leaf> placeholders that aren't plain files
DIR_LIKE = {"mount_point", "project", "repository", "repo", "new_root", "root", "destination", "target",
            "workspace", "venv", "build"}
NAMED = {
    "key": ["id_ed25519", "server.key"], "key_file": ["id_ed25519"], "private_key": ["id_ed25519"],
    "public_key": ["id_ed25519.pub"], "certificate": ["cert.pem"], "cert": ["cert.pem"],
    "image": ["photo.jpg", "logo.png"], "image_file": ["photo.jpg"], "video": ["clip.mp4"], "audio": ["song.mp3"],
    "binary": ["./app"], "executable": ["./app"], "output_executable": ["app"], "program": ["./app"],
    "archive": ["backup.tar.gz", "archive.zip"], "config": ["config.json"], "config_file": ["config.yml"],
    "script": ["deploy.sh"], "partition": ["/dev/sdb1"], "device": ["/dev/sdb"], "socket": ["/tmp/app.sock"],
    "dockerfile": ["Dockerfile"], "makefile": ["Makefile"], "requirements": ["requirements.txt"],
}

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

    def _path(self, ph: str) -> str:
        parts = re.split(r"[/\\]", ph)
        leaf = parts[-1].lower()
        stem = re.sub(r"\d+$", "", leaf.split(".")[0])
        if ph.startswith("~/") and len(parts) > 2:              # keep a real location: ~/.ssh/filename
            prefix = "/".join(parts[:-1]) + "/"
            return prefix + ("id_ed25519" if ".ssh" in prefix else self._file(leaf))
        if "dir" in stem or "folder" in stem or stem in DIR_LIKE:
            return self.rng.choice(DIR_NAMES)
        if stem == "file_or_directory":
            return self.rng.choice(DIR_NAMES) if self.rng.random() < 0.4 else self._file("x")
        if stem in NAMED:
            return self.rng.choice(NAMED[stem])
        return self._file(leaf)

    def _list(self, ph: str) -> str | None:
        """`{{path/to/file1 path/to/file2 ...}}` → two concrete names; lists of unknown things are skipped."""
        first = ph.replace("...", " ").split()[0] if ph.replace("...", " ").split() else ""
        if first.startswith(("path/to/", "path\\to\\", "~/")):
            a, b = self._path(first), self._path(first)
            if b == a:                                  # some extensions have one stock name: number them
                stem, dot, ext = a.partition(".")
                a, b = f"{stem}1{dot}{ext}", f"{stem}2{dot}{ext}"
            return f"{a} {b}"
        base = re.sub(r"\d+$", "", first.lower())
        if base in ("file", "files"):
            return " ".join(self._file("x" + self.rng.choice([".txt", ".log"])) for _ in range(2))
        if base in VALUES:
            return self.rng.choice(VALUES[base])
        return None

    def one(self, ph: str) -> str | None:
        ph = ph.strip()
        if ph.startswith("[") and ph.endswith("]") and "|" in ph:          # {{[-C|--directory]}}: an option spelling
            short, long = ph[1:-1].split("|", 1)
            return short if self.bsd or self.rng.random() < 0.7 else long   # BSD tools rarely take --long
        if "..." in ph:                                                         # "file1 file2 ...": a list
            return self._list(ph)
        if ph.startswith(("path/to/", "path\\to\\", "~/", "/path/")):
            return self._path(ph)
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


# Everyday tools only: thousands of tldr pages are niche tools, and a small model trained on them learns to
# invent flags for things nobody has installed. First word of the page name must be in this set.
EVERYDAY = set("""
find grep sed awk tar zip unzip gzip gunzip zcat du df ls cp mv rm mkdir rmdir touch cat less head tail sort uniq
wc cut tr xargs ln chmod chown ps kill killall pkill pgrep top htop lsof curl wget ssh scp rsync ping traceroute dig
nslookup netstat ss ip ifconfig whoami whereis file stat diff cmp date cal echo printf env history alias tee watch
crontab jq sudo man open pbcopy pbpaste defaults launchctl networksetup mdfind caffeinate screencapture softwareupdate
diskutil brew say pmset scutil apt apt-get dpkg systemctl journalctl service useradd usermod passwd free uptime
hostname uname lsblk mount umount xdg-open git docker kubectl npm npx yarn pip pip3 python python3 node make ffmpeg
tree realpath basename dirname seq shuf base64 md5sum sha256sum shasum md5 openssl nc ssh-keygen gpg nl column paste
comm split rev sleep nohup nice lscpu nproc mktemp tac locate rename gh code
""".split())
# Windows: cmd.exe built-ins only work in cmd; Verb-Noun cmdlets only in PowerShell; other .exe tools in both
CMD_BUILTINS = set("dir copy del erase move ren rename rd rmdir md mkdir type cd chdir cls set echo start pushd popd "
                   "mklink assoc ver vol title".split())
WINDOWS_TOOLS = CMD_BUILTINS | set("xcopy robocopy findstr where tasklist taskkill ipconfig ping netstat tracert "
                                   "nslookup shutdown winget choco scoop sfc tree systeminfo whoami hostname attrib fc "
                                   "clip curl tar netsh icacls certutil".split())
_CMDLET = re.compile(r"^[A-Z][a-z]+-[A-Z]\w+$")


def everyday(tool: str, platform: str) -> bool:
    first = tool.split()[0] if tool.split() else ""
    if platform == "windows":
        return bool(_CMDLET.match(first)) or first.lower() in WINDOWS_TOOLS
    return first.lower() in EVERYDAY


def windows_shells(tool: str) -> list[str]:
    first = tool.split()[0] if tool.split() else ""
    if _CMDLET.match(first):
        return ["powershell"]
    return ["cmd"] if first.lower() in CMD_BUILTINS else ["powershell", "cmd"]


# GNU-only spellings that break on macOS's BSD tools: such `common` examples are kept for Linux only
GNU_ONLY = re.compile(r"--max-depth|-daystart|--wildcards|-printf\b|\bsed -i(?! '')|\bstat -c\b|"
                      r"--human-readable|--total\b|\bdu\b.*--all|--color=always|--recursive\b|"
                      r"--fixed-strings|--binary-files|--with-filename|--line-number|--context")


def iter_examples(root: Path, seed: int = 13, platforms=tuple(PLATFORMS), all_tools: bool = False):
    """Yields dicts: {id, platform, os, shell, tool, description, command, values}. Also yields
    {'_unknown': [...]} for skipped examples so callers can report them."""
    rng = random.Random(seed)
    pages = root / "pages" if (root / "pages").is_dir() else root
    for plat in platforms:
        for page in sorted((pages / plat).glob("*.md")):
            tool, examples = parse_page(page.read_text(encoding="utf-8", errors="replace"))
            if not all_tools and not everyday(tool, plat):
                continue
            targets = [("Windows", sh) for sh in windows_shells(tool)] if plat == "windows" else PLATFORMS[plat]
            for i, (desc, template) in enumerate(examples):
                for os_name, shell in targets:
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
                    yield {"id": f"{plat}/{page.stem}#{i}/{os_name}/{shell}", "platform": plat, "os": os_name,
                           "shell": shell, "tool": tool, "description": desc, "command": cmd, "values": values}


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("root", help="a tldr-pages checkout (or its pages/ folder)")
    ap.add_argument("--out", default="data/out/tldr.jsonl")
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--all-tools", action="store_true", help="keep niche tools too (default: everyday tools only)")
    ap.add_argument("--max-per-tool", type=int, default=60,
                    help="examples per tool family per OS, so git/docker/npm don't crowd out the basics")
    ap.add_argument("--max-per-platform", default="",
                    help="e.g. common=3000,osx=1000,linux=1500,windows=800 (random sample per tldr folder)")
    a = ap.parse_args()
    caps = {k: int(v) for k, v in (x.split("=") for x in a.max_per_platform.split(",") if x)}

    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kept, per_os, unknown, by_plat = 0, Counter(), Counter(), {}
    per_tool = Counter()
    for ex in iter_examples(Path(a.root), a.seed, all_tools=a.all_tools):
        if "_unknown" in ex:
            unknown.update(ex["_unknown"])
            continue
        if ex["command"].startswith("tldr "):                  # "see also" pointers, not commands
            continue
        family = (ex["os"], ex["tool"].split()[0].lower())     # git, docker, npm…: one family, one budget
        if per_tool[family] >= a.max_per_tool:
            continue
        per_tool[family] += 1
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
