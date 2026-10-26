"""Generated everyday tasks, each one *executed* in a throwaway folder and kept only if it really works.

  python data/synth.py --out data/out/synth.jsonl --per-template 60

Targets what base models get wrong: relative paths ("here" means .), counting idioms, macOS (BSD) vs Linux
(GNU) flags, multi-step requests ("into a new folder" → mkdir first) and piped input.

Every example gets its own random files (never the eval's), so the model learns to read the folder listing
instead of memorising names. POSIX commands are verified on the machine you run this on: GNU variants on
Linux, BSD variants on macOS (run it on your Mac with --os macOS to verify those). PowerShell variants are
written by hand and not executed here; they're marked verified=false.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "eval"))

from run_eval import check  # noqa: E402  (same checks as the eval)

WORDS = ["apple", "river", "orbit", "maple", "falcon", "copper", "ember", "harbor", "violet", "cedar", "pixel",
         "tundra", "cobalt", "willow", "saffron", "quartz", "lagoon", "summit", "breeze", "nectar"]
TXT = ["diary", "ideas", "journal", "shopping", "reading", "memo", "draft", "story", "plan", "goals"]
LOGS = ["server", "worker", "api", "sync", "cron", "deploy"]
CSV = ["people", "orders", "scores", "inventory", "members"]
SRC_DIRS = ["lib", "code", "app", "scripts", "pkg"]
DOC_DIRS = ["papers", "manuals", "invoices", "slides"]
TXT_DIRS = ["chapters", "parts", "pieces", "sections"]
JUNK_DIRS = ["tmp", "cache", "dist", "out"]
NEW_DIRS = ["archive", "saved", "copies", "keep", "old", "stash"]
NAMES = ["mia", "leo", "zoe", "omar", "ivy", "raj", "eva", "noah"]
LEVELS = ["ERROR", "WARN", "INFO"]

# folders the folder index would know: (what you call it, where it usually lives under home).
# Never the eval's own (anime, tax stuff, backup), so the eval measures the pattern, not memory.
NICKS = [("movies", "Videos"), ("bills", "Documents"), ("receipts", "Documents/Finance"),
         ("holiday pics", "Pictures"), ("wallpapers", "Pictures"), ("comics", "Media"), ("manga", "Media/Books"),
         ("lecture notes", "Documents/Uni"), ("recipes", "Documents"), ("old photos", "Pictures/Archive"),
         ("work stuff", "Dropbox"), ("music", "Media"), ("podcasts", "Media/Audio"), ("ebooks", "Books"),
         ("scans", "Documents"), ("contracts", "Documents/Work"), ("screenshots", "Pictures"),
         ("drawings", "Art"), ("tutorials", "Videos/Learning"), ("game clips", "Videos"), ("songs", "Music"),
         ("research", "Work"), ("reports", "Work/2025"), ("designs", "Projects/Client"), ("memes", "Pictures")]
KNAMES = ["onepiece", "bleach", "trip", "beach", "chapter", "lecture", "invoice", "report", "summer", "draft",
          "intro", "final", "berlin", "tokyo", "wedding", "q3", "episode", "track"]
KEXTS = ["pdf", "jpg", "mp4", "png", "mp3", "txt", "mkv", "epub"]
HOME_STD = ["Downloads", "Desktop", "Documents"]


@dataclass
class World:
    """One random little project folder; slot values the templates can use."""
    rng: random.Random
    slots: dict = field(default_factory=dict)

    def build(self, root: Path, git: bool = False) -> None:
        r, s = self.rng, self.slots
        s["txt"] = r.choice(TXT) + ".txt"
        s["kw"] = r.choice(WORDS)
        other = [w for w in WORDS if w != s["kw"]]
        lines = [r.choice(other) for _ in range(r.randint(4, 9))]
        k = r.randint(1, 3)
        for i in r.sample(range(len(lines)), k):
            lines[i] = f"{s['kw']} {r.choice(other)}"
        lines.append(lines[0])                                  # one duplicate for `sort -u`
        s["txt_lines"], s["kw_count"] = lines, k + (1 if lines[0].startswith(s["kw"]) else 0)
        s["n_lines"], s["n_words"] = len(lines), sum(len(l.split()) for l in lines)
        (root / s["txt"]).write_text("\n".join(lines) + "\n")

        s["logdir"] = r.choice(["logs", "log", "var"])
        s["log"] = f"{s['logdir']}/{r.choice(LOGS)}.log"
        log = [f"2026-0{r.randint(1, 9)}-1{r.randint(0, 9)} 1{r.randint(0, 9)}:0{i % 10}:00 {r.choice(LEVELS)} "
               f"{r.choice(WORDS)}" for i in range(r.randint(5, 10))]
        s["log_lines"] = log
        s["n_errors"] = sum(" ERROR " in l for l in log)
        (root / s["logdir"]).mkdir()
        (root / s["log"]).write_text("\n".join(log) + "\n")

        s["csv"] = r.choice(CSV) + ".csv"
        people = r.sample(NAMES, 4)
        rows = [("name", "age", "city")] + [(p, str(r.randint(18, 70)), r.choice(WORDS)) for p in people]
        s["csv_rows"], s["person"] = rows, people[1]
        (root / s["csv"]).write_text("\n".join(",".join(x) for x in rows) + "\n")

        s["src"] = r.choice(SRC_DIRS)
        s["ext"] = r.choice(["py", "js"])
        names = r.sample(["main", "util", "db", "auth", "cli", "api"], r.randint(2, 4))
        s["src_files"] = [f"{n}.{s['ext']}" for n in names]
        (root / s["src"]).mkdir()
        for i, n in enumerate(s["src_files"]):
            body = f"# TODO {r.choice(WORDS)}\n" if i == 0 else ""
            (root / s["src"] / n).write_text(body + f"x = {i}\n")
        (root / s["src"] / "README.md").write_text("docs\n")
        s["todo_file"] = s["src_files"][0]

        s["docs"] = r.choice(DOC_DIRS)
        (root / s["docs"]).mkdir()
        s["big"] = f"{r.choice(WORDS)}.pdf"
        s["small"] = f"{r.choice([w for w in WORDS if w + '.pdf' != s['big']])}.pdf"
        with open(root / s["docs"] / s["big"], "wb") as fh:
            fh.write(b"%PDF" + os.urandom(2 * 1024 * 1024 + 4096))   # just over 2 MB
        (root / s["docs"] / s["small"]).write_text("%PDF small\n")

        s["tdir"] = r.choice(TXT_DIRS)
        (root / s["tdir"]).mkdir()
        s["parts"] = [f"{p}.txt" for p in r.sample(["one", "two", "three", "four"], 2)]
        for p in s["parts"]:
            (root / s["tdir"] / p).write_text(p.split(".")[0] + "\n")

        s["junk"] = r.choice(JUNK_DIRS)
        (root / s["junk"]).mkdir()
        (root / s["junk"] / "x.o").write_text("x")
        s["spaced"] = f"{r.choice(TXT)} {r.choice(['copy', 'final', 'v2'])}.txt"
        (root / s["spaced"]).write_text("space\n")
        s["new"] = r.choice(NEW_DIRS)
        s["n"] = r.randint(2, 4)
        s["word"] = r.choice(["done", "checked", "ok", "finished"])
        s["repl"] = r.choice([w for w in WORDS if w != s["kw"]]) + "x"
        s["branch"] = r.choice(["feature/login", "fix/typo", "dev", "experiment"])
        s["msg"] = r.choice(["update docs", "fix tests", "add config"])
        s["msg2"] = r.choice(["tidy up", "first draft", "wip", "add readme"])
        s["port"] = r.choice([3000, 3001, 4200, 5000, 5173, 8000, 8080, 8888, 9000])
        s["proc"] = r.choice(["node", "python", "java", "ruby", "nginx", "postgres", "redis-server"])
        if git:
            g = lambda *a: subprocess.run(["git", *a], cwd=root, check=True, capture_output=True)
            g("init", "-q", "-b", "main")
            g("config", "user.email", "synth@boop"); g("config", "user.name", "synth")
            g("add", s["src"]); g("commit", "-q", "-m", "first commit")
            g("add", s["txt"]); g("commit", "-q", "-m", f"add {s['txt']}")
            s["git_msgs"] = [f"add {s['txt']}", "first commit"]
            with open(root / s["txt"], "a") as fh:            # an uncommitted edit, for stash/diff/restore
                fh.write(f"{s['word']}\n")

    def build_home(self, home: Path, n_known: int, os_name: str) -> None:
        """~/Downloads, ~/Desktop, ~/Documents with a few files, plus n_known nicknamed folders somewhere
        under home that the folder index would list as `Known folders: nick → path`."""
        r, s = self.rng, self.slots
        s["hdir"] = r.choice(HOME_STD)
        s["hdir_l"] = s["hdir"].lower()
        s["hext"] = r.choice(["pdf", "zip", "png", "dmg" if os_name == "macOS" else "deb", "csv"])
        hfiles = [f"{r.choice(WORDS)}-{i}.{s['hext']}" for i in range(r.randint(2, 4))]
        hfiles = sorted(set(hfiles))
        for d in HOME_STD:
            (home / d).mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(hfiles):
            (home / s["hdir"] / f).write_text(f"{i}\n")
            os.utime(home / s["hdir"] / f, (1_700_000_000 + i * 1000,) * 2)    # newest = last in the list
        (home / s["hdir"] / f"notes-{r.choice(WORDS)}.txt").write_text("x\n")
        os.utime(home / s["hdir"] / next(p for p in os.listdir(home / s["hdir"]) if p.startswith("notes")),
                 (1_600_000_000,) * 2)
        s["hfiles"], s["hnewest"] = hfiles, hfiles[-1]
        s["known"] = []
        for k in range(n_known):
            suffix = "" if k == 0 else "2"
            nick, parent = r.choice([x for x in NICKS if x[0] != s.get("knick")])
            base = r.choice([nick.title(), nick, nick.replace(" ", "_"), nick.replace(" ", "-"), nick.title()])
            rel = f"{parent}/{base}"
            kdir = home / rel
            kdir.mkdir(parents=True)
            kname = r.choice(KNAMES)
            kext = r.choice(KEXTS)
            named = [f"{kname}-{i:02d}.{kext}" for i in range(1, r.randint(3, 4))]
            others = [f"{r.choice([n for n in KNAMES if n != kname])}-{i}.{kext}" for i in range(r.randint(3, 5))]   # 6+ files: never the size of ~ itself
            odd = f"{kname}-notes.{r.choice([e for e in KEXTS if e != kext])}"
            for f in named + others + [odd]:
                (kdir / f).write_text(f + "\n")
            with open(kdir / named[0], "wb") as fh:            # one big one, for "over 1mb" requests
                fh.write(os.urandom(1024 * 1024 + 50_000))
            if os_name == "Windows":
                path = "C:\\Users\\sam\\" + rel.replace("/", "\\")
                quoted = f'"{path}"' if " " in path else path
            else:
                path = ("$HOME/" if " " in rel else "~/") + rel
                quoted = f'"{path}"' if " " in rel else path
            s.update({f"knick{suffix}": nick, f"krel{suffix}": rel, f"kq{suffix}": quoted, f"kname{suffix}": kname,
                      f"kext{suffix}": kext, f"knamed{suffix}": named, f"kfiles{suffix}": sorted(named + others + [odd]),
                      f"kbase{suffix}": base.replace(" ", "_")})
            s["known"].append([nick, [path]])


@dataclass
class T:
    """A task template. Requests/commands use {slot} names from World; check follows eval/run_eval.py."""
    id: str
    requests: list
    linux: str
    check: dict
    macos: str | None = None          # when BSD needs a different command
    powershell: str | None = None     # hand-written, not executed here
    explain: str = ""
    git: bool = False
    pipe: str | None = None           # slot of a file whose content is piped in
    known: int = 0                    # nicknamed folders the index knows (1, 2); -1: home folders, no index
    run: bool = True                  # False: can't be checked in a sandbox (ports, processes); kept unverified


def ev(fn):
    """Lazily computed expected value from the world's slots."""
    return fn


TEMPLATES = [
    T("count-lines", ["how many lines does {txt} have", "line count of {txt}", "number of lines in {txt}?"],
      "wc -l < {txt}", {"check": "number", "expect": ev(lambda s: s["n_lines"])},
      powershell="(Get-Content {txt}).Count", explain="Counts the lines in {txt}."),
    T("count-words", ["how many words are in {txt}", "word count for {txt}"],
      "wc -w < {txt}", {"check": "number", "expect": ev(lambda s: s["n_words"])},
      powershell="(Get-Content {txt} | Measure-Object -Word).Words", explain="Counts the words in {txt}."),
    T("count-kw", ["in {txt}, how many lines mention {kw}", "count lines with {kw} in {txt}",
                   "how often does {kw} show up in {txt} (lines)"],
      "grep -c '{kw}' {txt}", {"check": "number", "expect": ev(lambda s: s["kw_count"])},
      powershell="(Select-String -Path {txt} -Pattern '{kw}').Count",
      explain="Counts lines in {txt} that contain {kw}."),
    T("count-files", ["how many files are inside {src}", "count the files in the {src} folder",
                      "number of files in {src}/"],
      "ls {src} | wc -l", {"check": "number", "expect": ev(lambda s: len(s["src_files"]) + 1)},
      powershell="(Get-ChildItem {src} -File).Count", explain="Counts the entries in {src}."),
    T("count-ext", ["how many .{ext} files are there here, including subfolders",
                    "count all the {ext} files under this folder"],
      "find . -name '*.{ext}' | wc -l", {"check": "number", "expect": ev(lambda s: len(s["src_files"]))},
      powershell="(Get-ChildItem -Recurse -Filter *.{ext}).Count", explain="Counts .{ext} files below here."),
    T("find-ext", ["list every .{ext} file in here and below", "where are my {ext} files"],
      "find . -name '*.{ext}'", {"check": "lines", "expect": ev(lambda s: [f"{s['src']}/{f}" for f in s["src_files"]])},
      powershell="Get-ChildItem -Recurse -Filter *.{ext}", explain="Lists .{ext} files below the current folder."),
    T("find-big", ["any files over 1mb in this folder or below?", "show files larger than 1 MB",
                   "find big files, more than a megabyte"],
      "find . -type f -size +1M", {"check": "lines", "expect": ev(lambda s: [f"{s['docs']}/{s['big']}"])},
      powershell="Get-ChildItem -Recurse -File | Where-Object Length -gt 1MB", explain="Lists files over 1 MB."),
    T("largest", ["what's the biggest file in here (subfolders too)", "single largest file below this folder"],
      "find . -type f -exec ls -s {{}} + | sort -rn | head -n 1",
      {"check": "first", "expect": ev(lambda s: s["big"])},
      powershell="Get-ChildItem -Recurse -File | Sort-Object Length -Descending | Select-Object -First 1",
      explain="Lists files with their sizes, biggest first, and keeps the top one."),
    T("size-human", ["how big is {docs}/{big}, in mb or whatever is readable",
                     "size of {docs}/{big} in human units"],
      "du -h {docs}/{big}", {"check": "contains", "expect": ev(lambda s: ["2", s["big"]])},
      powershell="'{{0:N1}} MB' -f ((Get-Item {docs}\\{big}).Length / 1MB)", explain="Shows the file's size, human-readable."),
    T("size-bytes", ["exact size of {txt} in bytes", "how many bytes is {txt}"],
      "wc -c < {txt}", {"check": "number", "expect": ev(lambda s: len(("\n".join(s["txt_lines"]) + "\n").encode()))},
      macos="stat -f %z {txt}", powershell="(Get-Item {txt}).Length", explain="Prints the size of {txt} in bytes."),
    T("tail", ["last {n} lines of {log}", "show me the end of {log}, {n} lines"],
      "tail -n {n} {log}", {"check": "lines", "ordered": True, "expect": ev(lambda s: s["log_lines"][-s["n"]:])},
      powershell="Get-Content {log} -Tail {n}", explain="Shows the last {n} lines of {log}."),
    T("head", ["first {n} lines of {txt}", "peek at the top {n} lines of {txt}"],
      "head -n {n} {txt}", {"check": "lines", "ordered": True, "expect": ev(lambda s: s["txt_lines"][:s["n"]])},
      powershell="Get-Content {txt} -TotalCount {n}", explain="Shows the first {n} lines of {txt}."),
    T("grep-errors", ["show only the ERROR lines from {log}", "which lines in {log} are errors"],
      "grep ERROR {log}", {"check": "lines", "ordered": True,
                           "expect": ev(lambda s: [l for l in s["log_lines"] if " ERROR " in l])},
      powershell="Select-String -Path {log} -Pattern 'ERROR' -CaseSensitive", explain="Prints the ERROR lines of {log}."),
    T("count-errors", ["how many errors are logged in {log}", "number of ERROR entries in {log}"],
      "grep -c ERROR {log}", {"check": "number", "expect": ev(lambda s: s["n_errors"])},
      powershell="(Select-String -Path {log} -Pattern 'ERROR' -CaseSensitive).Count",
      explain="Counts the ERROR lines in {log}."),
    T("unique", ["{txt} without duplicate lines, sorted", "dedupe the lines of {txt} and show them"],
      "sort -u {txt}", {"check": "lines", "expect": ev(lambda s: sorted(set(s["txt_lines"])))},
      powershell="Get-Content {txt} | Sort-Object -Unique", explain="Sorts {txt} and drops repeated lines."),
    T("csv-col", ["just the age column of {csv}", "print column 2 from {csv}"],
      "cut -d, -f2 {csv}", {"check": "lines", "ordered": True, "expect": ev(lambda s: [r[1] for r in s["csv_rows"]])},
      powershell="Import-Csv {csv} | Select-Object -ExpandProperty age",
      explain="Prints the second comma-separated column of {csv}."),
    T("csv-row", ["the row for {person} in {csv}", "find {person} in {csv}"],
      "grep '{person}' {csv}", {"check": "lines", "expect": ev(lambda s: [",".join(r) for r in s["csv_rows"] if r[0] == s["person"]])},
      powershell="Import-Csv {csv} | Where-Object name -eq '{person}'", explain="Prints the {csv} line for {person}."),
    T("todo", ["any TODOs left in {src}?", "list the TODO comments under {src}"],
      "grep -rn TODO {src}", {"check": "contains", "expect": ev(lambda s: [s["todo_file"], "TODO"])},
      powershell="Select-String -Path {src}\\* -Pattern 'TODO'", explain="Searches {src} for TODO, with line numbers."),
    T("only-dirs", ["which folders are in here", "list just the directories in this folder"],
      "ls -d */", {"check": "lines", "expect": ev(lambda s: sorted({s["logdir"], s["src"], s["docs"], s["tdir"], s["junk"]}))},
      powershell="Get-ChildItem -Directory", explain="Lists only the folders here."),
    T("du-dirs", ["how much space does each folder here take", "disk usage per directory in here"],
      "du -sh */", {"check": "contains", "expect": ev(lambda s: [s["docs"], s["src"]])},
      powershell="Get-ChildItem -Directory | ForEach-Object {{ '{{0,10:N0}} KB  {{1}}' -f ((Get-ChildItem $_ -Recurse -File | Measure-Object Length -Sum).Sum / 1KB), $_.Name }}",
      explain="Shows the total size of each folder."),
    T("spaces", ["find files with spaces in the name", "any filenames here containing a space?"],
      "find . -name '* *'", {"check": "contains", "expect": ev(lambda s: [s["spaced"]])},
      powershell="Get-ChildItem -Recurse | Where-Object Name -like '* *'", explain="Finds names containing a space."),
    T("show", ["what's in {csv}", "print {csv} to the screen"],
      "cat {csv}", {"check": "contains", "expect": ev(lambda s: [s["person"]])},
      powershell="Get-Content {csv}", explain="Prints {csv}."),
    T("mkdir", ["create a folder named {new}", "new directory called {new} please"],
      "mkdir -p {new}", {"check": "verify", "verify": "test -d {new}"},
      powershell="New-Item -ItemType Directory -Force {new}", explain="Creates the folder {new}."),
    T("copy-new", ["copy the .{ext} files from {src} into a new folder {new}",
                   "put copies of all {ext} files in {src} into a fresh {new} folder"],
      "mkdir -p {new} && cp {src}/*.{ext} {new}/",
      {"check": "verify", "verify": "test -f {new}/{todo_file} && test -f {src}/{todo_file}"},
      powershell="New-Item -ItemType Directory -Force {new} | Out-Null; Copy-Item {src}\\*.{ext} {new}",
      explain="Creates {new} and copies the .{ext} files from {src} into it."),
    T("move-new", ["move the txt files in {tdir} to a new folder {new}", "shift everything .txt from {tdir} into {new} (make it)"],
      "mkdir -p {new} && mv {tdir}/*.txt {new}/",
      {"check": "verify", "verify": "test -f {new}/{part0} && test ! -e {tdir}/{part0}"},
      powershell="New-Item -ItemType Directory -Force {new} | Out-Null; Move-Item {tdir}\\*.txt {new}",
      explain="Creates {new} and moves the .txt files from {tdir} into it."),
    T("rename-ext", ["change every .txt in {tdir} to .md", "rename the txt files inside {tdir} so they end in .md"],
      "for f in {tdir}/*.txt; do mv \"$f\" \"${{f%.txt}}.md\"; done",
      {"check": "verify", "verify": "test -f {tdir}/{part0_stem}.md && test ! -e {tdir}/{part0}"},
      powershell="Get-ChildItem {tdir} -Filter *.txt | Rename-Item -NewName {{ $_.Name -replace '\\.txt$', '.md' }}",
      explain="Renames each .txt file in {tdir} to .md."),
    T("merge", ["join all the txt files in {tdir} into combined.txt", "concatenate {tdir}/*.txt into one file combined.txt"],
      "cat {tdir}/*.txt > combined.txt", {"check": "verify", "verify": "grep -q {part0_stem} combined.txt"},
      powershell="Get-Content {tdir}\\*.txt | Set-Content combined.txt", explain="Writes all the .txt files in {tdir} into combined.txt."),
    T("append", ["add a line '{word}' at the end of {txt}", "append {word} to {txt}"],
      "echo '{word}' >> {txt}", {"check": "verify", "verify": "test \"$(tail -n 1 {txt})\" = {word}"},
      powershell="Add-Content {txt} '{word}'", explain="Adds the line '{word}' to the end of {txt}."),
    T("replace", ["in {txt}, swap {kw} for {repl}", "replace all {kw} with {repl} inside {txt}"],
      "sed -i 's/{kw}/{repl}/g' {txt}", {"check": "verify", "verify": "! grep -q {kw} {txt} && grep -q {repl} {txt}"},
      macos="sed -i '' 's/{kw}/{repl}/g' {txt}",
      powershell="(Get-Content {txt}) -replace '{kw}', '{repl}' | Set-Content {txt}",
      explain="Replaces every {kw} with {repl} in {txt}, in place."),
    T("targz", ["make a tar.gz of {docs}", "compress {docs} into {docs}.tar.gz"],
      "tar -czf {docs}.tar.gz {docs}", {"check": "verify", "verify": "tar -tzf {docs}.tar.gz | grep -q {small}"},
      powershell="tar -czf {docs}.tar.gz {docs}", explain="Packs {docs} into {docs}.tar.gz."),
    T("zip", ["zip up {src}", "create {src}.zip from the {src} folder"],
      "zip -qr {src}.zip {src}", {"check": "verify", "verify": "unzip -l {src}.zip | grep -q {todo_file}"},
      powershell="Compress-Archive -Path {src} -DestinationPath {src}.zip", explain="Zips {src} into {src}.zip."),
    T("rm-ext", ["delete the .o files in {junk}", "remove all *.o from {junk}"],
      "rm -f {junk}/*.o", {"check": "verify", "verify": "test ! -e {junk}/x.o && test -d {junk}", "danger": True},
      powershell="Remove-Item {junk}\\*.o", explain="Deletes the .o files in {junk}."),
    T("rm-dir", ["get rid of the {junk} folder completely", "delete {junk} and everything inside"],
      "rm -rf {junk}", {"check": "verify", "verify": "test ! -e {junk}", "danger": True},
      powershell="Remove-Item -Recurse -Force {junk}", explain="Deletes {junk} and all its contents."),
    # git
    T("git-branch", ["what branch is this", "name of the current git branch"],
      "git branch --show-current", {"check": "contains", "expect": ev(lambda s: ["main"])}, git=True,
      powershell="git branch --show-current", explain="Prints the current branch name."),
    T("git-log", ["last 2 commit messages", "show the messages of the two latest commits"],
      "git log -n 2 --format=%s", {"check": "lines", "ordered": True, "expect": ev(lambda s: s["git_msgs"])}, git=True,
      powershell="git log -n 2 --format=%s", explain="Prints the subjects of the last 2 commits."),
    T("git-new-branch", ["start a new branch {branch} and switch to it", "create and check out {branch}"],
      "git switch -c {branch}", {"check": "verify", "verify": "test \"$(git branch --show-current)\" = {branch}"},
      git=True, powershell="git switch -c {branch}", explain="Creates the branch {branch} and switches to it."),
    T("git-commit-all", ["stage everything and commit with message '{msg}'", "commit all changes as '{msg}'"],
      "git add -A && git commit -m '{msg}'", {"check": "verify", "verify": "test \"$(git log -1 --format=%s)\" = '{msg}'"},
      git=True, powershell="git add -A; git commit -m '{msg}'", explain="Stages all changes and commits them."),
    T("git-uncommitted", ["what have I changed but not committed", "list uncommitted changes"],
      "git status --short", {"check": "contains", "expect": ev(lambda s: [s["csv"]])}, git=True,
      powershell="git status --short", explain="Lists changed and untracked files."),
    # piped input: the data arrives on stdin
    T("pipe-count", ["count the lines with {kw}", "how many of these mention {kw}"],
      "grep -c '{kw}'", {"check": "number", "expect": ev(lambda s: s["kw_count"])}, pipe="txt",
      powershell="($input | Select-String -Pattern '{kw}').Count", explain="Counts piped lines containing {kw}."),
    T("pipe-errors", ["only the errors", "keep just the ERROR lines"],
      "grep ERROR", {"check": "lines", "ordered": True, "expect": ev(lambda s: [l for l in s["log_lines"] if " ERROR " in l])},
      pipe="log", powershell="$input | Select-String -Pattern 'ERROR' -CaseSensitive", explain="Keeps the ERROR lines."),
    T("pipe-col", ["just the second column", "give me field 2 of each row"],
      "cut -d, -f2", {"check": "lines", "ordered": True, "expect": ev(lambda s: [r[1] for r in s["csv_rows"]])},
      pipe="csv", powershell="$input | ForEach-Object {{ ($_ -split ',')[1] }}", explain="Prints the second comma-separated field."),
    T("pipe-unique", ["sort these and drop duplicates", "unique lines only, sorted"],
      "sort -u", {"check": "lines", "expect": ev(lambda s: sorted(set(s["txt_lines"])))}, pipe="txt",
      powershell="$input | Sort-Object -Unique", explain="Sorts the input and removes duplicates."),

    # ---- several conditions at once (small models drop one of them) --------------------------------
    T("find-ext-big", ["pdfs bigger than 1mb in {docs}", "find pdf files over 1 MB inside {docs}",
                       "which pdfs in {docs} are larger than a megabyte"],
      "find {docs} -type f -name '*.pdf' -size +1M", {"check": "lines", "expect": ev(lambda s: [f"{s['docs']}/{s['big']}"])},
      powershell="Get-ChildItem {docs} -Recurse -Filter *.pdf | Where-Object Length -gt 1MB",
      explain="Lists PDF files in {docs} larger than 1 MB."),
    T("find-ext-name", ["{ext} files in {src} with {stem} in the name", "find {src} {ext} files named like {stem}"],
      "find {src} -type f -name '*{stem}*.{ext}'", {"check": "lines", "expect": ev(lambda s: [f"{s['src']}/{s['todo_file']}"])},
      powershell="Get-ChildItem {src} -Recurse -Filter '*{stem}*.{ext}'",
      explain="Finds .{ext} files in {src} whose name contains {stem}."),

    # ---- your home folders, by their usual names -----------------------------------------------------
    T("home-list", ["what's in my {hdir_l}", "list my {hdir_l} folder", "show everything in {hdir_l}"],
      "ls ~/{hdir}", {"check": "contains", "expect": ev(lambda s: s["hfiles"])}, known=-1,
      powershell="Get-ChildItem \"$HOME\\{hdir}\"", explain="Lists the files in ~/{hdir}."),
    T("home-ext", ["all the {hext} files in my {hdir_l}", "find {hext}s in {hdir_l}", "{hext} files sitting in my {hdir_l} folder"],
      "find ~/{hdir} -type f -name '*.{hext}'", {"check": "names", "expect": ev(lambda s: s["hfiles"])}, known=-1,
      powershell="Get-ChildItem \"$HOME\\{hdir}\" -Recurse -Filter *.{hext}", explain="Finds .{hext} files in ~/{hdir}."),
    T("home-newest", ["newest file in my {hdir_l}", "what did I last save to {hdir_l}", "most recent thing in my {hdir_l} folder"],
      "ls -t ~/{hdir} | head -n 1", {"check": "first", "expect": ev(lambda s: s["hnewest"])}, known=-1,
      powershell="Get-ChildItem \"$HOME\\{hdir}\" | Sort-Object LastWriteTime -Descending | Select-Object -First 1",
      explain="Shows the most recently modified file in ~/{hdir}."),
    T("home-copy", ["copy {txt} to my {hdir_l}", "put a copy of {txt} on my {hdir_l}"],
      "cp {txt} ~/{hdir}/", {"check": "verify", "verify": "test -f \"$HOME/{hdir}/{txt}\" && test -f {txt}"}, known=-1,
      powershell="Copy-Item {txt} \"$HOME\\{hdir}\"", explain="Copies {txt} into ~/{hdir}."),

    # ---- folders the index knows: "my {knick} folder" → the path in `Known folders` -----------------
    T("known-find", ["get all {kext} files named {kname} from my {knick} folder", "{kname} {kext}s in the {knick} folder",
                     "find {kext} files with {kname} in the name in my {knick} folder",
                     "every {kname} {kext} in {knick}"],
      "find {kq} -type f -iname '*{kname}*.{kext}'", {"check": "names", "expect": ev(lambda s: s["knamed"])}, known=1,
      powershell="Get-ChildItem {kq} -Recurse -Filter '*{kname}*.{kext}'",
      explain="Finds .{kext} files with {kname} in the name in your {knick} folder."),
    T("known-count", ["how many files are in my {knick} folder", "count the files in {knick}", "number of files in the {knick} folder"],
      "ls {kq} | wc -l", {"check": "number", "expect": ev(lambda s: len(s["kfiles"]))}, known=1,
      powershell="(Get-ChildItem {kq} -File).Count", explain="Counts the files in your {knick} folder."),
    T("known-list", ["what's in my {knick} folder", "show me the {knick} folder", "list {knick}"],
      "ls {kq}", {"check": "contains", "expect": ev(lambda s: s["kfiles"])}, known=1,
      powershell="Get-ChildItem {kq}", explain="Lists your {knick} folder."),
    T("known-big", ["{kext} files over 1mb in my {knick} folder", "big {kext}s (more than 1 MB) in {knick}"],
      "find {kq} -type f -name '*.{kext}' -size +1M", {"check": "names", "expect": ev(lambda s: s["knamed"][:1])},
      known=1, powershell="Get-ChildItem {kq} -Recurse -Filter *.{kext} | Where-Object Length -gt 1MB",
      explain="Lists .{kext} files over 1 MB in your {knick} folder."),
    T("known-size", ["how big is my {knick} folder", "total size of the {knick} folder"],
      "du -sh {kq}", {"check": "contains", "expect": ev(lambda s: [s["krel"].rsplit("/", 1)[-1]])}, known=1,
      powershell="'{{0:N1}} MB' -f ((Get-ChildItem {kq} -Recurse -File | Measure-Object Length -Sum).Sum / 1MB)",
      explain="Shows the total size of your {knick} folder."),
    T("known-copy-to", ["copy {txt} to my {knick} folder", "put {txt} into the {knick} folder", "save a copy of {txt} in {knick}"],
      "cp {txt} {kq}/", {"check": "verify", "verify": "test -f \"$HOME/{krel}/{txt}\" && test -f {txt}"}, known=1,
      powershell="Copy-Item {txt} {kq}", explain="Copies {txt} into your {knick} folder."),
    T("known-move-here", ["move the pdfs from {docs} into my {knick} folder", "shift {docs}/*.pdf over to {knick}"],
      "mv {docs}/*.pdf {kq}/", {"check": "verify", "verify": "test -f \"$HOME/{krel}/{big}\" && test ! -e {docs}/{big}"},
      known=1, powershell="Move-Item {docs}\\*.pdf {kq}", explain="Moves the PDFs in {docs} into your {knick} folder."),
    T("known-cd", ["go to my {knick} folder", "take me to {knick}", "cd into the {knick} folder"],
      "cd {kq}", {"check": "exit0"}, known=1, powershell="Set-Location {kq}", explain="Changes to your {knick} folder."),
    T("known-zip", ["zip my {knick} folder", "make a zip of the {knick} folder"],
      "zip -qr {kbase}.zip {kq}", {"check": "verify", "verify": "unzip -l {kbase}.zip | grep -q {kname}"}, known=1,
      powershell="Compress-Archive -Path {kq} -DestinationPath {kbase}.zip", explain="Zips your {knick} folder into {kbase}.zip."),
    T("known-two", ["copy the {kext} files from my {knick} folder to {knick2}",
                    "copy all {kext}s in {knick} over to my {knick2} folder"],
      "cp {kq}/*.{kext} {kq2}/", {"check": "verify", "verify": "test -f \"$HOME/{krel2}/{knamed1}\""}, known=2,
      powershell="Copy-Item {kq}\\*.{kext} {kq2}", explain="Copies the .{kext} files from {knick} to {knick2}."),

    # ---- git beyond the basics ----------------------------------------------------------------------
    T("git-undo-soft", ["take back my last commit but keep the edits", "uncommit the latest commit, leave my work staged",
                        "revert the previous commit locally without losing anything"],
      "git reset --soft HEAD~1", {"check": "verify",
                                  "verify": "test \"$(git log -1 --format=%s)\" = 'first commit' && git diff --cached --name-only | grep -q {txt}"},
      git=True, powershell="git reset --soft HEAD~1", explain="Undoes the last commit and keeps its changes staged."),
    T("git-amend", ["change the last commit message to '{msg2}'", "reword my latest commit as '{msg2}'"],
      "git commit --amend -m '{msg2}'", {"check": "verify", "verify": "test \"$(git log -1 --format=%s)\" = '{msg2}'"},
      git=True, powershell="git commit --amend -m '{msg2}'", explain="Replaces the last commit's message."),
    T("git-stash", ["stash my changes", "put my uncommitted work aside for now"],
      "git stash", {"check": "verify", "verify": "test -z \"$(git diff --name-only)\" && git stash list | grep -q ."},
      git=True, powershell="git stash", explain="Saves your uncommitted changes and cleans the working tree."),
    T("git-diff-file", ["what did I change in {txt}", "show my edits to {txt} since the last commit"],
      "git diff {txt}", {"check": "contains", "expect": ev(lambda s: ["+" + s["word"]])}, git=True,
      powershell="git diff {txt}", explain="Shows uncommitted changes to {txt}."),
    T("git-discard", ["throw away my changes to {txt}", "undo my edits in {txt}, go back to the committed version"],
      "git restore {txt}", {"check": "verify", "verify": "test -z \"$(git diff --name-only)\"", "danger": True},
      git=True, powershell="git restore {txt}", explain="Discards uncommitted changes to {txt}."),

    # ---- ports and processes (can't be proven in a sandbox: written carefully, kept unverified) -------
    T("port-who", ["what's running on port {port}", "which app has port {port} open", "who is listening on {port}"],
      "lsof -nP -iTCP:{port} -sTCP:LISTEN", {"check": "exit0"}, run=False,
      powershell="Get-NetTCPConnection -LocalPort {port} | Select-Object LocalPort, State, OwningProcess",
      explain="Shows the process listening on TCP port {port}."),
    T("port-kill", ["kill whatever is on port {port}", "free up port {port}", "stop the thing using port {port}"],
      "lsof -ti tcp:{port} | xargs kill", {"check": "exit0", "danger": True}, run=False,
      powershell="Stop-Process -Id (Get-NetTCPConnection -LocalPort {port}).OwningProcess",
      explain="Stops the process listening on port {port}."),
    T("proc-find", ["is {proc} running", "show the {proc} processes"],
      "pgrep -fl {proc}", {"check": "exit0"}, run=False,
      powershell="Get-Process {proc} -ErrorAction SilentlyContinue", explain="Lists running processes matching {proc}."),
    T("proc-kill", ["kill all {proc} processes", "stop every {proc} process"],
      "pkill {proc}", {"check": "exit0", "danger": True}, run=False,
      powershell="Stop-Process -Name {proc}", explain="Stops every process named {proc}."),
]

_CWD = {"Linux": "/home/sam/project", "macOS": "/Users/sam/project", "Windows": "C:\\Users\\sam\\project"}


def _fmt(text: str, s: dict) -> str:
    return text.format(**s)


def make_examples(t: T, n: int, os_name: str, rng: random.Random, verify: bool):
    """Yields (example dict, ok, note)."""
    from boop.context import listing

    shell = {"Linux": "bash", "macOS": "zsh", "Windows": "powershell"}[os_name]
    cmd_t = t.powershell if os_name == "Windows" else (t.macos if os_name == "macOS" and t.macos else t.linux)
    if cmd_t is None:
        return
    for i in range(n):
        with tempfile.TemporaryDirectory(prefix="boop-synth-") as d:
            root = Path(d) / "work"
            root.mkdir()
            home = Path(d) / "home"
            home.mkdir()
            w = World(random.Random(rng.random()))
            w.build(root, git=t.git)
            w.build_home(home, max(0, t.known), os_name)
            s = dict(w.slots)
            s["stem"] = s["todo_file"].rsplit(".", 1)[0]
            s["knamed1"] = s["knamed"][0] if t.known == 2 else ""      # a file that known-two copies over
            s["part0"] = s["parts"][0]
            s["part0_stem"] = s["parts"][0].rsplit(".", 1)[0]
            if os_name == "Windows":                     # Windows paths in the request read naturally either way
                s = {k: (v.replace("/", "\\") if isinstance(v, str) and k in ("log",) else v) for k, v in s.items()}
            request = _fmt(rng.choice(t.requests), s)
            command = _fmt(cmd_t, s)
            spec = {k: (v(w.slots) if callable(v) else v) for k, v in t.check.items()}
            if "verify" in spec:
                spec["verify"] = _fmt(spec["verify"], s)
            piped = None
            if t.pipe:
                text = (root / w.slots[t.pipe]).read_text()
                piped = "\n".join(text.splitlines()[:20])
            ex = {"id": f"synth/{t.id}/{os_name}/{i}", "source": "synth", "template": t.id, "os": os_name,
                  "shell": shell, "cwd": _CWD[os_name], "files": listing(root), "request": request,
                  "command": command, "explanation": _fmt(t.explain, s), "piped": piped,
                  "danger": bool(spec.get("danger")), "verified": False,
                  "known": s["known"] if t.known > 0 else []}
            ok, note = True, ""
            if verify and os_name != "Windows" and t.run:
                stdin = open(root / w.slots[t.pipe], "rb") if t.pipe else subprocess.DEVNULL
                try:
                    r = subprocess.run(["bash" if shell == "bash" else "zsh" if shutil.which("zsh") else "bash",
                                        "-c", command], cwd=root, capture_output=True, text=True, timeout=15,
                                       stdin=stdin, env={**os.environ, "HOME": str(home)})
                    ok = r.returncode == 0 if spec["check"] == "exit0" else check(spec, r.stdout, root, shell, home)
                    note = "" if ok else (r.stderr.strip()[-120:] or r.stdout.strip()[-120:])
                finally:
                    if t.pipe:
                        stdin.close()
                ex["verified"] = ok
            yield ex, ok, note


def native_os() -> str:
    return "macOS" if sys.platform == "darwin" else "Linux"


def main() -> None:
    ap = argparse.ArgumentParser(formatter_class=argparse.RawDescriptionHelpFormatter, description=__doc__)
    ap.add_argument("--out", default="data/out/synth.jsonl")
    ap.add_argument("--per-template", type=int, default=60)
    ap.add_argument("--os", action="append", help="Linux / macOS / Windows (default: all three)")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--only", default="")
    a = ap.parse_args()

    oses = a.os or ["Linux", "macOS", "Windows"]
    rng = random.Random(a.seed)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    stats, fails = Counter(), Counter()
    with out.open("w", encoding="utf-8") as fh:
        for t in TEMPLATES:
            if a.only not in t.id:
                continue
            if t.id == "zip" and not shutil.which("zip"):
                continue
            for os_name in oses:
                # Linux can also prove macOS examples that use the same portable command; BSD-only ones
                # (t.macos) are verified only when you run this on a Mac
                verify = os_name == native_os() or (os_name == "macOS" and t.macos is None)
                for ex, ok, note in make_examples(t, a.per_template, os_name, rng, verify):
                    if not ok:
                        fails[(t.id, os_name, note)] += 1
                        continue
                    fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
                    stats[os_name] += 1
    print(f"wrote {sum(stats.values())} examples {dict(stats)} → {out}", file=sys.stderr)
    for (tid, os_name, note), c in fails.most_common():
        print(f"  ✗ {tid} [{os_name}] ×{c}: {note}", file=sys.stderr)


if __name__ == "__main__":
    main()
