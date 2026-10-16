"""The prompt (shared by the CLI, the eval harness and, later, fine-tuning data) and the answer format."""
from __future__ import annotations

import json
import os
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
    # kept apart from eval/tasks.jsonl on purpose (tests/test_prompt.py checks), so the eval can't be copied
    "posix": [
        ("which process is using port 3000",
         {"command": "lsof -nP -iTCP:3000 -sTCP:LISTEN",
          "explanation": "Shows the process listening on TCP port 3000."}),
        ("show the 10 newest files in my downloads folder",
         {"command": "ls -t ~/Downloads | head -n 10",
          "explanation": "Lists the 10 most recently modified files in ~/Downloads."}),
    ],
    "powershell": [
        ("which process is using port 3000",
         {"command": "Get-NetTCPConnection -LocalPort 3000 | Select-Object LocalPort, State, OwningProcess",
          "explanation": "Shows the process using port 3000."}),
        ("show the 10 newest files in my downloads folder",
         {"command": "Get-ChildItem \"$HOME\\Downloads\" | Sort-Object LastWriteTime -Descending | Select-Object -First 10",
          "explanation": "Lists the 10 most recently modified files in Downloads."}),
    ],
    "cmd": [
        ("which process is using port 3000",
         {"command": "netstat -ano | findstr :3000",
          "explanation": "Shows connections on port 3000 with the owning process ID."}),
        ("show the newest files in my downloads folder",
         {"command": "dir /o-d \"%USERPROFILE%\\Downloads\"",
          "explanation": "Lists Downloads with the newest files first."}),
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


def use_examples() -> bool:
    """Few-shot examples help a general model; a boop fine-tune learned the format and is faster without them.
    BOOP_FEWSHOT=1/0 overrides the guess from the model name."""
    flag = os.environ.get("BOOP_FEWSHOT")
    if flag is not None:
        return flag not in ("0", "false", "no")
    model = (os.environ.get("BOOP_MODEL") or "") + (os.environ.get("BOOP_API_MODEL") or "")
    return "boop" not in model.lower()


def build_messages(request: str, ctx: Context, examples: bool | None = None) -> list[dict]:
    """The prompt at inference *and* in training data (examples=False there), so they always match."""
    msgs = [{"role": "system", "content": SYSTEM}]
    if use_examples() if examples is None else examples:
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
