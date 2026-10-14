# boop 🐶 — nudge your shell

Say what you want in plain English. boop shows you the command. Press Enter to run it.

```console
$ boop find pdfs over 10mb in downloads
  find ~/Downloads -type f -name '*.pdf' -size +10M
  [⏎] run  [e] edit  [c] copy  [x] explain  [q] quit

$ boop undo my last commit but keep the changes
  git reset --soft HEAD~1

$ boop delete all node_modules folders here
  find . -name node_modules -type d -prune -exec rm -rf {} +
  ⚠ deletes the files it finds
  type 'yes' to run, e to edit, anything else to cancel:
```

- **Runs on your machine.** A small open model (Qwen2.5-Coder 1.5B) via llama.cpp: no account, no API key, nothing leaves your computer. About 1.9 GB, downloaded once.
- **Nothing runs without you.** Enter runs a safe command; anything that deletes, overwrites, force-pushes or needs admin needs you to type `yes`. Those checks are plain rules, not the model, so they don't get creative.
- **Every shell.** zsh, bash, fish and PowerShell on macOS, Linux and Windows (cmd.exe works too, with fewer tricks).

## Four ways in

```bash
boop find big log files                    # 1. one request
boop                                       # 2. English mode: keeps going until you type exit
cat server.log | boop count errors per hour   # 3. pipe data in
show disk usage sorted   ⌥⏎                # 4. hotkey: type English at your prompt, press Alt/Option+Enter
```

English mode remembers the last few steps, so "now zip them" works. In a pipe, boop shows the model only the first few lines; the whole input goes to the command.

Other handy flags: `boop -p …` prints just the command (for scripts, or `| sh`), `boop -y …` runs safe commands without asking, `boop -x 'tar -xzvf a.tgz'` explains a command.

## Install

```bash
pipx install boop-cli          # or: pip install boop-cli
```

Then add the shell integration. It lets `cd`, `export` and history work, and adds the hotkey:

| Shell | Add this line to | Line |
|---|---|---|
| zsh | `~/.zshrc` | `eval "$(boop --init zsh)"` |
| bash | `~/.bashrc` | `eval "$(boop --init bash)"` |
| fish | `~/.config/fish/config.fish` | `boop --init fish \| source` |
| PowerShell | `$PROFILE` | `Invoke-Expression (& boop --init powershell \| Out-String)` |

Hotkey: **Alt/Option+Enter** in zsh, bash 4+ and fish; **Ctrl+G** in PowerShell, because Windows Terminal uses Alt+Enter for full screen. On macOS Terminal/iTerm2, turn on "Use Option as Meta key" for the Option+Enter hotkey.

Without the integration boop still works, but it runs the command itself, so a `cd` won't stick.

**Windows:** `llama-cpp-python` needs a prebuilt wheel or a C++ compiler. The easy path is
`pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu`.
Alternatively, skip it and point boop at Ollama (below).

## Use a different model

```bash
export BOOP_MODEL=~/models/my-model.gguf                    # any local GGUF
export BOOP_API_BASE=http://localhost:11434/v1              # or any OpenAI-compatible server (Ollama, LM Studio…)
export BOOP_API_MODEL=qwen2.5-coder:7b
```

## How good is it?

`eval/run_eval.py` runs 34 everyday tasks: files, text, archives, git, and risky deletes. Each one runs the model's command in a throwaway folder and checks the result, so any correct command passes, not one exact string.

```bash
python eval/run_eval.py
```

## Develop

```bash
pip install -e ".[dev]" && pytest
BOOP_BACKEND=fake BOOP_FAKE='{"pdf": "find . -name *.pdf"}' boop find pdfs   # UI without a model
```

MIT licence. The default model, Qwen2.5-Coder-1.5B-Instruct, is Apache-2.0.
