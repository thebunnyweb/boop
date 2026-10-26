# boop 🐶 — nudge your shell

Say what you want in plain English. boop shows you the command. Press Enter to run it.

![boop in a terminal](docs/boop.gif)

```console
$ boop find pdfs over 10mb in downloads
╭─ boop ─────────────────────────────────────────────── ~/code/site · zsh ─╮
│ ❯ find pdfs over 10mb in downloads                                       │
│ ✦ boop · 0.8s  2 past ────────────────────────────────────────────────── │
│ ▌ find ~/Downloads -type f -name '*.pdf' -size +10M                      │
│   find . -name '*.pdf'                              list pdfs in here    │
│   du -sh ~/Downloads/*.pdf                          how big are my pdfs  │
│                                                                          │
│ ──────────────────────────────────────────────────────────────────────── │
│ Finds PDF files bigger than 10 MB in Downloads.                          │
╰─ ⏎ run  ⇥ edit  ^Y copy  esc quit ───────────────────────────────────────╯
```

The panel opens under your prompt and disappears when you're done, leaving one line: `❯ the command`.
As you type, it fuzzy-searches what you ran before (instant, no model); Enter on **✦ ask boop** asks the
model. Risky commands (deletes, overwrites, force-push, sudo) show a red warning and need you to type `yes`.

- **Runs on your machine.** A small open model (Qwen2.5-Coder 1.5B) via llama.cpp: no account, no API key, nothing leaves your computer. About 1.9 GB, downloaded once.
- **Nothing runs without you.** Enter runs a safe command; anything that deletes, overwrites, force-pushes or needs admin needs you to type `yes`. Those checks are plain rules, not the model, so they don't get creative.
- **Every shell.** zsh, bash, fish and PowerShell on macOS, Linux and Windows (cmd.exe works too, with fewer tricks).

## Four ways in

```bash
boop find big log files                    # 1. one request
boop                                       # 2. English mode: a new panel after each command, esc to leave
cat server.log | boop count errors per hour   # 3. pipe data in
show disk usage sorted   ⌥⏎                # 4. hotkey: type English at your prompt, press Alt/Option+Enter
```

English mode opens a fresh panel after each command until you press esc. It remembers the last few steps,
so "now zip them" works. Keys: ↑↓ (or Ctrl-P/N) move, ⏎ asks or runs, ⇥ edits the command first, Ctrl-Y
copies, esc goes back or quits. `BOOP_PLAIN=1` swaps the panel for plain line-by-line prompts. In a pipe, boop shows the model only the first few lines; the whole input goes to the command.

Other handy flags: `boop -p …` prints just the command (for scripts, or `| sh`), `boop -y …` runs safe commands without asking, `boop -x 'tar -xzvf a.tgz'` explains a command.

## It knows your folders

"get all pdfs named naruto from my anime folder" works even when that folder is `~/Media/Anime`. boop keeps a
small index of your folder **names** (never file names or contents), refreshed in the background once a day,
and tells the model only about the folders a request mentions:

```text
Known folders: anime → ~/Media/Anime
```

If one name matches several folders, the panel offers the command for each. The index covers your home folder,
skips hidden folders, caches, `node_modules`, `Library` and the like, and lives in `~/.local/state/boop/`
(`%LOCALAPPDATA%\boop` on Windows). `boop --reindex` rebuilds it now; `BOOP_INDEX_ROOTS` picks other roots;
`BOOP_NO_INDEX=1` turns it off.

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

## Use a different model (any provider)

boop's own small model runs locally by default. To use something else, run `boop --init-config` and edit
`~/.config/boop/config.toml` (`%APPDATA%\boop\config.toml` on Windows):

```toml
use = "claude"

[providers.claude]
preset = "anthropic"            # pip install "boop-cli[anthropic]"
model = "claude-opus-5-5"
api_key_env = "ANTHROPIC_API_KEY"

[providers.home]
preset = "ollama"
model = "qwen2.5-coder:7b"

[providers.work]
preset = "openai"               # any OpenAI-compatible API; set base_url for a gateway, vLLM, LiteLLM…
model = "…"
api_key_cmd = "security find-generic-password -s boop-openai -w"    # keys from Keychain, 1Password, pass…
```

Presets: `local`, `ollama`, `lmstudio`, `llama-server`, `openai`, `anthropic`, `gemini`, `openrouter`, `groq`,
`mistral`, `deepseek`, `together`, `azure`. `boop --providers` lists them, `boop --use home …` switches for one
run, `BOOP_PROVIDER=home` for a shell.

**Corporate networks.** boop trusts the operating system's certificate store (so CAs your IT department
installed just work), honours `HTTPS_PROXY` / `NO_PROXY`, and takes extras in the config:

```toml
[network]
ca_bundle = "~/certs/corp-root.pem"    # added to the public CAs, not instead of them
proxy = "http://proxy.corp:8080"
no_proxy = "localhost,.corp"
```

`boop --doctor` checks the key, the proxy, the TLS handshake (and who issued the certificate) and makes one
real request, and says what to change when something fails.

## How good is it?

`eval/run_eval.py` runs 34 everyday tasks: files, text, archives, git, and risky deletes. Each one runs the model's command in a throwaway folder and checks the result, so any correct command passes, not one exact string.

```bash
python eval/run_eval.py
```

## Train your own small model

boop ships with a general model, but it's built to run its own fine-tune: SmolLM2-360M, about 270–390 MB, which answers instantly on any CPU.
Everything runs for free on Kaggle:

1. `data/kaggle_data.ipynb` builds the data. tldr-pages examples are rephrased into casual requests by a teacher model, then combined with
   synthetic everyday tasks that are **executed in a sandbox and kept only if they work** (`data/synth.py`).
2. `training/kaggle_train.ipynb` fine-tunes with Unsloth, exports GGUF (Q8_0 and Q4_K_M), pushes to Hugging Face, and scores
   each file on `eval/`.

Then: `export BOOP_MODEL=bunnyweb/boop-360m-gguf BOOP_MODEL_FILE=<file>.gguf`. boop skips the few-shot examples for
fine-tuned models automatically (`BOOP_FEWSHOT=0/1` to force it).

## Develop

```bash
pip install -e ".[dev]" && pytest
BOOP_BACKEND=fake BOOP_FAKE='{"pdf": "find . -name *.pdf"}' boop find pdfs   # UI without a model
```

MIT licence. The default model, Qwen2.5-Coder-1.5B-Instruct, is Apache-2.0.
