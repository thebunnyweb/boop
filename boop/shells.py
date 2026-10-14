"""Shell integration: `eval "$(boop --init zsh)"` in your rc file.

Why a shell function and not just the program: a program can't change the shell it was started from, so
`cd`, `export` and history would be lost. The function asks the engine for a command (written to a temp
file), then runs it *in your shell*. Protocol with the engine (`--out FILE`):
  exit 0 + FILE non-empty  → run that command here
  exit 10                  → leave English mode
  anything else            → nothing to run (cancelled, or boop already ran it, e.g. pipe mode)
"""
from __future__ import annotations

ZSH = r'''# boop — zsh integration. Add to ~/.zshrc:  eval "$(boop --init zsh)"
export BOOP_SESSION=$$
boop() {
  local __boop_out __boop_rc __boop_cmd __boop_first
  __boop_out=$(mktemp -t boop.XXXXXX) || return 1
  if (( $# == 0 )) && [[ -t 0 ]]; then            # `boop` alone: English mode
    __boop_first=--banner
    while true; do
      command boop --shell zsh --repl $__boop_first --out "$__boop_out"; __boop_rc=$?
      __boop_first=
      (( __boop_rc == 10 )) && break
      if [[ -s $__boop_out ]]; then
        __boop_cmd=$(<"$__boop_out"); : >| "$__boop_out"
        print -rs -- "$__boop_cmd"
        eval "$__boop_cmd"
      fi
    done
    rm -f "$__boop_out"; return 0
  fi
  command boop --shell zsh --out "$__boop_out" "$@"; __boop_rc=$?
  if [[ -s $__boop_out ]]; then
    __boop_cmd=$(<"$__boop_out"); rm -f "$__boop_out"
    print -rs -- "$__boop_cmd"
    eval "$__boop_cmd"; return $?
  fi
  rm -f "$__boop_out"; return $__boop_rc
}
__boop_widget() {                                   # Option/Alt+Enter: English on the line → command
  [[ -z $BUFFER ]] && return
  local __boop_err __boop_cmd
  __boop_err=$(mktemp -t boop.XXXXXX)
  zle -R "boop: thinking…"
  __boop_cmd=$(command boop --shell zsh -p --request="$BUFFER" 2>"$__boop_err")
  if [[ -n $__boop_cmd ]]; then BUFFER=$__boop_cmd; CURSOR=${#BUFFER}; fi
  [[ -s $__boop_err ]] && zle -M "$(<"$__boop_err")"
  rm -f "$__boop_err"
}
zle -N __boop_widget
bindkey '^[^M' __boop_widget
'''

BASH = r'''# boop — bash integration. Add to ~/.bashrc:  eval "$(boop --init bash)"
export BOOP_SESSION=$$
boop() {
  local __boop_out __boop_rc __boop_cmd __boop_first
  __boop_out=$(mktemp -t boop.XXXXXX) || return 1
  if [ $# -eq 0 ] && [ -t 0 ]; then               # `boop` alone: English mode
    __boop_first=--banner
    while true; do
      command boop --shell bash --repl $__boop_first --out "$__boop_out"; __boop_rc=$?
      __boop_first=
      [ $__boop_rc -eq 10 ] && break
      if [ -s "$__boop_out" ]; then
        __boop_cmd=$(cat "$__boop_out"); : >| "$__boop_out"
        history -s "$__boop_cmd"
        eval "$__boop_cmd"
      fi
    done
    rm -f "$__boop_out"; return 0
  fi
  command boop --shell bash --out "$__boop_out" "$@"; __boop_rc=$?
  if [ -s "$__boop_out" ]; then
    __boop_cmd=$(cat "$__boop_out"); rm -f "$__boop_out"
    history -s "$__boop_cmd"
    eval "$__boop_cmd"; return $?
  fi
  rm -f "$__boop_out"; return $__boop_rc
}
__boop_widget() {                                   # Alt+Enter (bash 4+): English on the line → command
  [ -z "$READLINE_LINE" ] && return
  local __boop_cmd
  __boop_cmd=$(command boop --shell bash -p --request="$READLINE_LINE")
  if [ -n "$__boop_cmd" ]; then READLINE_LINE=$__boop_cmd; READLINE_POINT=${#__boop_cmd}; fi
}
bind -x '"\e\C-m": __boop_widget' 2>/dev/null
'''

FISH = r'''# boop — fish integration. Add to ~/.config/fish/config.fish:  boop --init fish | source
set -gx BOOP_SESSION $fish_pid
function boop
    set -l out (mktemp -t boop.XXXXXX)
    if test (count $argv) -eq 0; and isatty stdin    # `boop` alone: English mode
        set -l first --banner
        while true
            command boop --shell fish --repl $first --out $out
            set -l rc $status
            set first
            test $rc -eq 10; and break
            if test -s $out
                set -l cmd (cat $out | string collect)
                printf '' >$out
                eval $cmd
            end
        end
        rm -f $out
        return 0
    end
    command boop --shell fish --out $out $argv
    set -l rc $status
    if test -s $out
        set -l cmd (cat $out | string collect)
        rm -f $out
        eval $cmd
        return $status
    end
    rm -f $out
    return $rc
end
function __boop_widget                               # Alt+Enter: English on the line → command
    set -l line (commandline)
    test -z "$line"; and return
    set -l cmd (command boop --shell fish -p --request="$line" | string collect)
    test -n "$cmd"; and commandline -r -- $cmd
    commandline -f repaint
end
bind \e\r __boop_widget
'''

POWERSHELL = r'''# boop — PowerShell integration. Add to $PROFILE:  Invoke-Expression (& boop --init powershell | Out-String)
$env:BOOP_SESSION = $PID
$global:__BoopExe = (Get-Command boop -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1).Source
function boop {
    $out = [System.IO.Path]::GetTempFileName()
    try {
        if ($args.Count -eq 0 -and -not $MyInvocation.ExpectingInput) {      # `boop` alone: English mode
            $first = @('--banner')
            while ($true) {
                & $global:__BoopExe --shell powershell --repl @first --out $out
                $rc = $LASTEXITCODE; $first = @()
                if ($rc -eq 10) { break }
                $cmd = Get-Content -Raw $out
                if ($cmd) {
                    Set-Content -Path $out -Value '' -NoNewline
                    [Microsoft.PowerShell.PSConsoleReadLine]::AddToHistory($cmd)
                    Invoke-Expression $cmd
                }
            }
            return
        }
        if ($MyInvocation.ExpectingInput) { $input | & $global:__BoopExe --shell powershell --out $out @args }
        else { & $global:__BoopExe --shell powershell --out $out @args }
        $cmd = Get-Content -Raw $out
        if ($cmd) {
            [Microsoft.PowerShell.PSConsoleReadLine]::AddToHistory($cmd)
            Invoke-Expression $cmd
        }
    } finally { Remove-Item $out -ErrorAction SilentlyContinue }
}
if (Get-Module PSReadLine) {                                                # Ctrl+G: English on the line → command
    Set-PSReadLineKeyHandler -Chord 'Ctrl+g' -BriefDescription 'boop' -ScriptBlock {
        $line = $null; $cursor = $null
        [Microsoft.PowerShell.PSConsoleReadLine]::GetBufferState([ref]$line, [ref]$cursor)
        if (-not $line) { return }
        $cmd = (& $global:__BoopExe --shell powershell -p "--request=$line" 2>$null | Out-String).Trim()
        if ($cmd) {
            [Microsoft.PowerShell.PSConsoleReadLine]::RevertLine()
            [Microsoft.PowerShell.PSConsoleReadLine]::Insert($cmd)
        }
    }
}
'''

SCRIPTS = {"zsh": ZSH, "bash": BASH, "fish": FISH, "powershell": POWERSHELL, "pwsh": POWERSHELL}

CMD_NOTE = """boop: cmd.exe has no shell functions, so there's nothing to install.
Just run `boop <what you want>`; boop runs the command itself (a `cd` won't stick after it exits).
For the full experience (cd, history, Ctrl+G hotkey) use PowerShell:  boop --init powershell"""


def init_script(shell: str) -> str:
    shell = shell.lower()
    if shell == "cmd":
        return CMD_NOTE
    if shell not in SCRIPTS:
        raise SystemExit(f"boop: no integration for '{shell}'. Supported: zsh, bash, fish, powershell (and cmd).")
    return SCRIPTS[shell]
