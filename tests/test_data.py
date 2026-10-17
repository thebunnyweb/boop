"""The training-data pipeline: tldr parsing, teacher validation, verified synthetic tasks, final format."""
import json
import random
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data"))
sys.path.insert(0, str(ROOT / "eval"))

import build  # noqa: E402
import synth  # noqa: E402
import teach  # noqa: E402
import tldr  # noqa: E402

FIX = ROOT / "tests/fixtures/tldr"


def test_parse_page_cleans_descriptions():
    name, ex = tldr.parse_page((FIX / "common/tar.md").read_text())
    assert name == "tar"
    assert ex[0][0] == "Create an archive and write it to a file"        # "[c]reate … [f]ile" cleaned up
    assert ex[0][1].startswith("tar cf {{")


def test_fill_gives_realistic_values_and_tracks_them():
    f = tldr.Filler(random.Random(1))
    cmd, values = f.fill("tar xf {{path/to/source.tar.ext}} {{[-C|--directory]}} {{path/to/directory}}")
    archive = cmd.split()[2]
    assert archive.endswith(".tar.gz") and archive in values
    assert "--directory" not in values and "-C" not in values            # flag spellings aren't values
    assert "{{" not in cmd


def test_macos_examples_never_use_gnu_only_flags():
    rows = [e for e in tldr.iter_examples(FIX) if "_unknown" not in e and e["os"] == "macOS"]
    assert rows and not any(tldr.GNU_ONLY.search(r["command"]) for r in rows)


def test_teacher_requests_must_mention_every_value():
    row = {"values": ["backup.tar.gz", "notes.txt sales.csv", "*.py"]}
    good = "pack notes.txt and sales.csv plus the py files into backup.tar.gz"
    assert teach.valid_requests(row, [good, "make a backup", good.upper()]) == [good]
    assert teach.parse('blah {"requests": ["a b c"]} blah') == ["a b c"]


def test_every_synthetic_template_really_works_on_this_machine(tmp_path):
    """Each template is executed in a sandbox; a broken one would teach the model a wrong command."""
    rng = random.Random(3)
    native = synth.native_os()
    failures = []
    for t in synth.TEMPLATES:
        if t.git and not shutil.which("git") or t.id == "zip" and not shutil.which("zip"):
            continue
        for ex, ok, note in synth.make_examples(t, 1, native, rng, verify=True):
            if not ok:
                failures.append((t.id, ex["command"], note))
    assert not failures, failures


def test_build_format_matches_boop_prompt_without_examples():
    ex = {"os": "macOS", "shell": "zsh", "cwd": "/Users/sam/p", "files": ["a.txt"], "request": "count lines in a.txt",
          "command": "wc -l < a.txt", "explanation": "Counts lines.", "source": "synth", "group": "synth:x"}
    msgs = build.to_chat(ex)["messages"]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant"]   # no few-shot turns
    assert json.loads(msgs[-1]["content"])["command"] == "wc -l < a.txt"
    assert "Folder contains: a.txt" in msgs[1]["content"]


def test_build_refuses_eval_lookalikes():
    assert build.eval_overlap(["count the lines in diary.txt"])            # eval: "count the lines in notes.txt"
    assert not build.eval_overlap(["how many lines does diary.txt have"])


def test_list_placeholders_never_loop_and_give_two_names():
    f = tldr.Filler(random.Random(0))
    for ph in ["path/to/file...", "path/to/file1.mp3 path/to/file2.mp3 ...", "path/to/directory1 path/to/directory2 ..."]:
        v = f.one(ph)
        assert v and len(v.split()) == 2 and v.split()[0] != v.split()[1], (ph, v)
    assert f.one("argument1 argument2 ...") is None                       # unknown list: skip the example


def test_paths_get_the_right_kind_of_name():
    f = tldr.Filler(random.Random(0))
    assert f.one("~/.ssh/filename") == "~/.ssh/id_ed25519"
    assert f.one("path/to/key") in tldr.NAMED["key"]
    assert f.one("path/to/mount_point") in tldr.DIR_NAMES


def test_only_everyday_tools_and_right_windows_shell():
    assert tldr.everyday("git commit", "common") and tldr.everyday("tar", "common")
    assert not tldr.everyday("sindresorhus", "common")
    assert tldr.windows_shells("Get-ChildItem") == ["powershell"]
    assert tldr.windows_shells("dir") == ["cmd"]                           # `dir /s` breaks in PowerShell
    assert tldr.windows_shells("robocopy") == ["powershell", "cmd"]


def test_gnu_long_options_are_never_labelled_macos():
    assert tldr.gnu_only_on_mac('alias la="ls --all"')
    assert tldr.gnu_only_on_mac("find . -name x | sort --reverse")
    assert not tldr.gnu_only_on_mac("ls -la | base64")
    assert not tldr.gnu_only_on_mac("brew bundle --file Brewfile")       # brew isn't a BSD core tool
    assert not tldr.gnu_only_on_mac("git log --oneline")


def test_build_drops_eval_lookalikes_instead_of_failing(tmp_path):
    synth_row = {"id": "s1", "source": "synth", "template": "count-lines", "os": "Linux", "shell": "bash",
                 "cwd": "/x", "files": ["diary.txt"], "request": "how many lines does diary.txt have",
                 "command": "wc -l < diary.txt", "explanation": "Counts.", "piped": None}
    look = {**synth_row, "id": "s2", "request": "count the lines in diary.txt"}
    f = tmp_path / "s.jsonl"
    f.write_text(json.dumps(synth_row) + "\n" + json.dumps(look) + "\n")
    out = tmp_path / "m"
    sys.argv = ["build.py", "--synth", str(f), "--out", str(out), "--valid-frac", "0"]
    build.main()
    rows = [json.loads(l) for l in (out / "train.jsonl").read_text().splitlines()]
    assert [r["messages"][1]["content"].endswith("how many lines does diary.txt have") for r in rows] == [True]
