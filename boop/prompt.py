"""The prompt (shared by the CLI, the eval harness and, later, fine-tuning data) and the answer format."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from .context import Context

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {"type": "string"},
        "explanation": {"type": "string"},
    },
    "required": ["command", "explanation"],
}

SYSTEM = """You turn a request in plain English into ONE command for the user's shell.
Reply with JSON only: {"command": "...", "explanation": "..."}
- command: a single line the user can paste and run as-is in their shell. Chain steps with the shell's own operators.
- explanation: one short sentence saying what it does.
- Use tools that ship with the OS. Respect the OS: macOS has BSD tools (e.g. `sed -i ''`, `stat -f`), Linux has GNU tools.
- Work relative to the current folder: write `notes.txt`, not the folder's full path.
- Quote paths that may contain spaces. Never invent file or folder names the user did not mention.
- Prefer the safe, reversible option when the request is ambiguous.
- If no command can do it, return an empty command and say why in the explanation."""

# a couple of examples per shell family keep a small model on format and dialect
_EXAMPLES = {
    "posix": [
        ("find pdfs bigger than 10mb in downloads",
         {"command": "find ~/Downloads -type f -name '*.pdf' -size +10M",
          "explanation": "Lists PDF files over 10 MB under ~/Downloads."}),
        ("undo my last git commit but keep the changes",
         {"command": "git reset --soft HEAD~1",
          "explanation": "Removes the last commit and keeps its changes staged."}),
    ],
    "powershell": [
        ("find pdfs bigger than 10mb in downloads",
         {"command": "Get-ChildItem \"$HOME\\Downloads\" -Recurse -Filter *.pdf | Where-Object Length -gt 10MB",
          "explanation": "Lists PDF files over 10 MB under Downloads."}),
        ("undo my last git commit but keep the changes",
         {"command": "git reset --soft HEAD~1",
          "explanation": "Removes the last commit and keeps its changes staged."}),
    ],
    "cmd": [
        ("find pdfs in downloads",
         {"command": "dir /s /b \"%USERPROFILE%\\Downloads\\*.pdf\"",
          "explanation": "Lists PDF files under Downloads."}),
        ("undo my last git commit but keep the changes",
         {"command": "git reset --soft HEAD~1",
          "explanation": "Removes the last commit and keeps its changes staged."}),
    ],
}

_SHELL_NAME = {"zsh": "zsh", "bash": "bash", "fish": "fish", "sh": "POSIX sh",
               "powershell": "PowerShell", "cmd": "Windows cmd.exe"}


def family(shell: str) -> str:
    return shell if shell in ("powershell", "cmd") else "posix"


def context_block(ctx: Context) -> str:
    lines = [f"OS: {ctx.os_detail}", f"Shell: {_SHELL_NAME.get(ctx.shell, ctx.shell)}", f"Folder: {ctx.cwd}"]
    if ctx.files:
        lines.append("Folder contains: " + ", ".join(ctx.files))
    if ctx.history:
        lines.append("Earlier in this session:")
        lines += [f"  - {h['request']}  →  {h['command']}" for h in ctx.history]
    if ctx.piped is not None:
        lines.append("The command will receive piped data on stdin (do not read a file instead). First lines:")
        lines += ["  | " + l for l in ctx.piped.splitlines()]
    return "\n".join(lines)


def user_message(request: str, ctx: Context) -> str:
    return f"{context_block(ctx)}\n\nRequest: {request.strip()}"


def build_messages(request: str, ctx: Context) -> list[dict]:
    msgs = [{"role": "system", "content": SYSTEM}]
    # examples share the real folder, so there's no made-up path for the model to copy
    ex_ctx = Context(os=ctx.os, shell=ctx.shell, cwd=ctx.cwd, files=ctx.files)
    for req, ans in _EXAMPLES[family(ctx.shell)]:
        msgs.append({"role": "user", "content": user_message(req, ex_ctx)})
        msgs.append({"role": "assistant", "content": json.dumps(ans)})
    msgs.append({"role": "user", "content": user_message(request, ctx)})
    return msgs


EXPLAIN = """Explain the user's shell command in plain English: what it does, then what each important part means.
Two or three short sentences. Mention anything risky (deletes, overwrites, needs admin).
Reply with JSON only: {"command": "<the command, unchanged>", "explanation": "..."}"""


def build_explain_messages(command: str, ctx: Context) -> list[dict]:
    return [{"role": "system", "content": EXPLAIN},
            {"role": "user", "content": f"OS: {ctx.os_detail}\nShell: {_SHELL_NAME.get(ctx.shell, ctx.shell)}\n\n"
                                        f"Command: {command}"}]


@dataclass
class Answer:
    command: str
    explanation: str


_FENCE = re.compile(r"```(?:\w+)?\s*\n?(.*?)```", re.S)


def parse_answer(text: str) -> Answer:
    """JSON when the model behaves; a fenced block or the first line when it doesn't."""
    text = (text or "").strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        try:
            d = json.loads(m.group(0))
            return Answer(str(d.get("command") or "").strip(), str(d.get("explanation") or "").strip())
        except json.JSONDecodeError:
            pass
    m = _FENCE.search(text)
    cmd = (m.group(1) if m else text).strip().splitlines()
    return Answer(cmd[0].strip() if cmd else "", "")
