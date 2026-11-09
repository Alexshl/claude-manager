# claude-manager (`cm`)

[English](README.md) | [Українська](README.uk.md)

Keep Claude Code sessions with **Remote Control** alive on a home machine, so they show up in Claude Desktop on another computer, and start, stop and resume them from there.

Why: Remote Control sessions die when the terminal closes, the machine reboots or the process crashes. `cm` runs each session in tmux, remembers which sessions you want, and a small daemon brings them back (with `--resume`) after any failure. No network servers, no open ports.

## How it works

```
 Claude Desktop (other computer)
        |  Remote Control
        v
 +--------------------------- home machine ---------------------------+
 |  launchd / systemd --user                                           |
 |      +-- cm daemon  (checks every 30s, restarts dead sessions)      |
 |             |  reads                                                |
 |             v                                                       |
 |      ~/.config/claude-manager/state.json   (desired sessions)       |
 |             |  starts                                               |
 |             v                                                       |
 |      tmux session "cm"                                              |
 |        +-- window: manager   claude --remote-control "<host>: manager"
 |        +-- window: <id8>     claude --remote-control "<host>: proj" |
 |        +-- ...                                                     |
 +---------------------------------------------------------------------+
```

The permanent `manager` session runs in this repo's `control/` folder; its CLAUDE.md teaches it to use `cm`, so you can manage the machine by chatting with it from Claude Desktop.

## Quick install

Requires Claude Code installed and logged in (`curl -fsSL https://claude.ai/install.sh | bash`, then run `claude` once).

```bash
curl -fsSL https://raw.githubusercontent.com/Alexshl/claude-manager/main/quick-install.sh | bash
```

The guided installer checks the OS and dependencies (git, tmux, Python 3.10+), offers to install what is missing, clones the repo to `~/.local/share/claude-manager/src`, asks for a host label and runs `install.sh`. Options: `--yes` / `CM_YES=1` (no prompts), `--dry-run` / `CM_DRY_RUN=1`, env `CM_DIR`, `CM_PYTHON`, `CM_HOST_LABEL`.

## Manual install

```bash
git clone https://github.com/Alexshl/claude-manager.git
cd claude-manager
./install.sh
```

This creates a venv in `~/.local/share/claude-manager/venv`, links `cm` into `~/.local/bin`, installs a launchd agent (macOS) or a systemd user unit plus `loginctl enable-linger` (Linux), and registers the control session. Safe to re-run. Needs `claude`, `tmux`, `git`, Python 3.10+.

## Usage

| Command | Description |
|---|---|
| `cm sessions [--dir X] [--limit N] [--json]` | saved Claude sessions (`~/.claude/projects`) |
| `cm ls [--json]` | managed sessions: name, id, cwd, alive/dead, restarts |
| `cm start <dir> [--name N] [--mode M] [--create] [--clone URL]` | new session; `--create` runs `mkdir -p`; `--clone` clones if the dir is absent (`owner/repo` uses `gh repo clone`, otherwise `git clone`) |
| `cm resume <id\|prefix> [--name N] [--mode M] [--fork\|--force]` | manage a saved session. Refuses if another `claude` process holds it; `--fork` continues it as a new session; `--force` terminates a plain process (never a Remote Control one) |
| `cm stop <id\|name>` | stop and forget a session (the `manager` session cannot be stopped) |
| `cm restart <id\|name>` | restart a session |
| `cm logs <id\|name> [-n N]` | recent terminal output |
| `cm send <id\|prefix\|name> <text\|-> [--wait N]` | send a message to a live managed session as one bracketed paste + Enter (`-` reads stdin; max 16000 chars; control chars rejected; never the `manager` session). Exit codes: 0 sent, 1 other error (also `manager`, ambiguous id, bad text), 3 not managed or dead, 4 dialog/permission prompt on screen or no own claude process in its window; use `--` before text starting with `-` (`--wait N` waits up to N s for a dialog to go) |
| `cm answer <id\|prefix\|name> (--option N \| --text -) [--expect-label L]` | answer an AskUserQuestion widget (one question, single choice) of a live managed session: arrow keys to item N (or to "Type something." for `--text -`, one line read from stdin), the cursor and typed text are verified before Enter, and the widget must close afterwards. Permission prompts, startup dialogs, multi-question/multi-select widgets and a non-matching `--expect-label` (label of option N must start with L) give exit 4 with no key pressed; 1 = bad arguments, N out of range, multi-line text, cursor/text not verified; 5 = Enter was sent but the widget did not close (the answer may have gone through); 3 not managed or dead. `cm send` still refuses any widget |
| `cm who <id\|prefix\|name> [--json]` | JSON: `session_id`, `managed`, `alive`, `holders` (`pid`, `remote_control`, `ours`). `cm ls --json` also has `session_id`, `window`, `control` |
| `cm daemon [--once] [--interval 30]` | keep-alive loop (run by the service) |
| `cm control` | register the control session |
| `cm update` | `git pull --ff-only`, reinstall if `pyproject.toml` changed, restart the daemon (tmux sessions keep running) |

Permission modes: `acceptEdits`, `auto`, `bypassPermissions`, `default`, `dontAsk`, `plan`.

## Configuration

`~/.config/claude-manager/config.json` (honours `XDG_CONFIG_HOME`):

- `default_permission_mode`: mode for sessions without `--mode` (default `auto`)
- `host_label`: host part of session names (default: short hostname)
- `control_id`: fixed id of the control session (generated automatically)

Sessions appear in Claude Desktop as `<host>: <name>`, for example `macmini: fdesk`; the control session is `macmini: manager`. State lives in `state.json` next to the config; the daemon log is `~/.local/state/claude-manager/daemon.log`.

## Controlling from Claude Desktop

Open the `<host>: manager` session and ask in plain language: "list my sessions", "start a session for ~/projects/foo", "resume the last session in bar", "stop foo". It runs `cm` for you and asks before stopping anything.

## Troubleshooting

- Sessions do not show up: run `claude` once in a terminal and make sure you are logged in; check `cm ls` and `cm logs manager`.
- `cm: command not found`: add `~/.local/bin` to PATH.
- Session shows `dead` repeatedly: look at `cm logs <name>` and `daemon.log`; restarts use exponential backoff up to 10 minutes.
- `claude` not found by the service: re-run `./install.sh` from a shell where `claude` is on PATH.
- Linux: the service stops after logout: `sudo loginctl enable-linger $USER`.
- macOS: for an always-on machine enable automatic login and disable sleep (`sudo pmset -a sleep 0 disksleep 0`).

## Uninstall

```bash
./uninstall.sh          # keeps state and config
./uninstall.sh --purge  # also removes state and logs
```

## Security note

Sessions run unattended in `auto` permission mode by default, and `cm` marks their folders as trusted in `~/.claude.json` (a one-time backup goes to `~/.claude.json.cm-bak`). Anyone with access to your Claude account can drive those sessions. Set `default_permission_mode` to something stricter if that is not acceptable.

## Tests

```bash
python3 -m unittest discover -s tests -t .
```

## License

MIT
