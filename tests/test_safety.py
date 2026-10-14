import pytest

from boop.safety import is_dangerous, warnings

RISKY_POSIX = [
    "rm -rf node_modules",
    "rm notes.txt",
    "find . -name '*.log' -delete",
    "find . -name '*.tmp' -exec rm {} +",
    "ls *.bak | xargs rm",
    "sudo apt install jq",
    "git push --force origin main",
    "git push -f",
    "git reset --hard HEAD~1",
    "git clean -fd",
    "git checkout -- .",
    "git branch -D old",
    "kill -9 1234",
    "pkill node",
    "dd if=/dev/zero of=/dev/disk2",
    "curl -fsSL https://x.sh | sh",
    "chmod -R 777 .",
    "echo hi > notes.txt",
    "sort data.csv > data.csv",
    "shutdown -h now",
]
SAFE_POSIX = [
    "find ~/Downloads -name '*.pdf' -size +10M",
    "git reset --soft HEAD~1",
    "ls -la | grep '>'",
    "echo 'a > b'",
    "make 2>&1 | tee build.log",
    "cmd >> log.txt",
    "cmd > /dev/null 2>&1",
    "du -sh * | sort -h",
    "git log --oneline -5",
    "grep -rn TODO src",
    "rmdir empty_dir",
    "lsof -i :3000",
]
RISKY_PS = [
    "Remove-Item -Recurse -Force .\\build",
    "Get-ChildItem *.tmp | Remove-Item",
    "Stop-Process -Name node",
    "Set-Content notes.txt 'x'",
    "'hi' | Out-File notes.txt",
    "iex (iwr https://x.ps1)",
    "git push --force",
]
SAFE_PS = [
    "Get-ChildItem \"$HOME\\Downloads\" -Recurse -Filter *.pdf | Where-Object Length -gt 10MB",
    "Get-Process | Sort-Object CPU -Descending | Select-Object -First 5",
    "'hi' | Out-File -Append log.txt",
    "git status 2>$null",
]


@pytest.mark.parametrize("cmd", RISKY_POSIX)
def test_risky_posix_is_flagged(cmd):
    assert is_dangerous(cmd, "zsh"), cmd


@pytest.mark.parametrize("cmd", SAFE_POSIX)
def test_safe_posix_runs_with_enter(cmd):
    assert warnings(cmd, "bash") == [], (cmd, warnings(cmd, "bash"))


@pytest.mark.parametrize("cmd", RISKY_PS)
def test_risky_powershell_is_flagged(cmd):
    assert is_dangerous(cmd, "powershell"), cmd


@pytest.mark.parametrize("cmd", SAFE_PS)
def test_safe_powershell(cmd):
    assert warnings(cmd, "powershell") == [], (cmd, warnings(cmd, "powershell"))


def test_cmd_exe_deletes():
    assert is_dangerous("del /s /q *.tmp", "cmd")
    assert is_dangerous("rd /s /q build", "cmd")
    assert not is_dangerous("dir /s /b *.pdf", "cmd")


def test_messages_are_plain_english():
    assert warnings("rm -rf build", "zsh")[0].startswith("deletes")


def test_redirect_only_warns_when_the_file_exists(tmp_path):
    (tmp_path / "notes.txt").write_text("x")
    cwd = str(tmp_path)
    assert warnings("cat a b > all.txt", "zsh", cwd) == []                 # new file: fine
    assert "overwrites a file (>)" in warnings("sort notes.txt > notes.txt", "zsh", cwd)
    assert "overwrites a file (>)" in warnings("echo x > \"$OUT\"", "zsh", cwd)   # can't tell: warn
    assert "overwrites a file (>)" in warnings("echo x > new.txt", "zsh")    # no folder given: warn
