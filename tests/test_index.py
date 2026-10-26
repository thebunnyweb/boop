"""The folder index: build it on a fake home, then look up the folders requests talk about."""
import os

from boop import index
from boop.context import Context
from boop.prompt import Answer, build_messages, with_alternatives


def fake_home(tmp_path):
    home = tmp_path / "home"
    for d in ["Media/Anime", "Downloads/anime", "Documents/Tax Stuff", "Backups/laptop", "code/site/src",
              "code/site/node_modules/react", ".config/secret", "Library/Caches/x"]:
        (home / d).mkdir(parents=True)
    return home


def test_build_skips_hidden_junk_and_system_folders(tmp_path, monkeypatch):
    home = fake_home(tmp_path)
    monkeypatch.setenv("BOOP_INDEX_ROOTS", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert index.build() > 0
    paths = index.index_file().read_text().splitlines()
    assert str(home / "Media/Anime") in paths and str(home / "Documents/Tax Stuff") in paths
    assert not any("node_modules" in p or ".config" in p or "Library" in p for p in paths)


def test_lookup_finds_folders_by_name(tmp_path, monkeypatch):
    home = fake_home(tmp_path)
    monkeypatch.setenv("BOOP_INDEX_ROOTS", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    index.build()
    by_name = index.load()
    look = lambda req, **kw: index.lookup(req, index=by_name, home=str(home), **kw)
    # one name, two folders: shallowest first
    assert look("get all pdfs named naruto from my anime folder") == [("anime", ["~/Media/Anime", "~/Downloads/anime"])]
    assert look("how many files are in my tax stuff folder") == [("tax stuff", ["$HOME/Documents/Tax Stuff"])]
    assert look("copy notes.txt to my backup folder") == [("backup", ["~/Backups/laptop"])] or \
        look("copy notes.txt to my backups folder") == [("backups", ["~/Backups"])]
    assert look("count lines in app.log") == []
    assert look("undo my last commit") == []
    assert look("list files in src", local=["src/", "README.md"]) == []     # the folder here wins


def test_mentions():
    m = index.mentions
    assert m("find pdfs over 10mb in downloads") == ["downloads"]
    assert m("zip the invoices folder") == ["invoices"]
    assert m("rename all .jpeg to .jpg in the holiday pics folder") == ["holiday pics"]
    assert m("list files in ~/code") == [] and m("what's using port 3000") == []


def test_prompt_gets_the_known_folder_and_the_panel_gets_the_others():
    known = [("anime", ["~/Media/Anime", "~/Downloads/anime"])]
    ctx = Context(os="macOS", shell="zsh", cwd="/Users/sam", files=[], known=known)
    user = build_messages("pdfs named naruto from my anime folder", ctx, examples=False)[-1]["content"]
    assert "Known folders: anime → ~/Media/Anime" in user and "Downloads/anime" not in user
    ans = with_alternatives(Answer("find ~/Media/Anime -iname '*naruto*.pdf'", "x"), known)
    assert ans.alternatives == [("find ~/Downloads/anime -iname '*naruto*.pdf'", "same, in ~/Downloads/anime")]


def test_no_index_means_no_known_folders(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "nothing"))
    monkeypatch.delenv("BOOP_NO_INDEX", raising=False)
    assert index.lookup("files in my anime folder") == []
    monkeypatch.setenv("BOOP_NO_INDEX", "1")
    index.refresh_in_background()                     # disabled: must not start anything or create files
    assert not (tmp_path / "nothing").exists()
