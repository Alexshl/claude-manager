"""Thin tmux wrapper: one tmux session, one window per managed session."""
from __future__ import annotations

import shutil
import subprocess

from .config import TMUX_SESSION

HUB = "_hub"


class TmuxError(Exception):
    pass


def _run(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    if not shutil.which("tmux"):
        raise TmuxError("tmux not found on PATH")
    p = subprocess.run(["tmux", *args], capture_output=True, text=True)
    if check and p.returncode != 0:
        raise TmuxError(f"tmux {' '.join(args[:2])} failed: {p.stderr.strip()}")
    return p


def _has_session() -> bool:
    return _run("has-session", "-t", f"={TMUX_SESSION}", check=False).returncode == 0


def ensure_server() -> None:
    """Make sure the `cm` tmux session exists (with an idle hub window)."""
    if _has_session():
        return
    _run("new-session", "-d", "-s", TMUX_SESSION, "-n", HUB,
         "sh -c 'while :; do sleep 3600; done'")
    _run("set-option", "-t", TMUX_SESSION, "remain-on-exit", "off")


def _windows() -> list[tuple[str, str, bool]]:
    """[(window_id, window_name, pane_dead)] for the cm session."""
    if not _has_session():
        return []
    p = _run("list-panes", "-s", "-t", f"={TMUX_SESSION}", "-F",
             "#{window_id}\t#{window_name}\t#{pane_dead}", check=False)
    out = []
    for line in p.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            out.append((parts[0], parts[1], parts[2] == "1"))
    return out


def _find(name: str) -> tuple[str, bool] | None:
    # Look up by window id: window names made of digits would be read as indexes.
    for wid, wname, dead in _windows():
        if wname == name:
            return wid, dead
    return None


def start_window(name: str, cwd: str, cmd: str) -> None:
    ensure_server()
    if _find(name):
        kill(name)
    _run("new-window", "-d", "-t", f"={TMUX_SESSION}:", "-n", name, "-c", cwd, cmd)
    found = _find(name)
    if found:
        _run("set-option", "-w", "-t", found[0], "remain-on-exit", "off", check=False)


def is_alive(name: str) -> bool:
    found = _find(name)
    return bool(found and not found[1])


def kill(name: str) -> None:
    found = _find(name)
    if found:
        _run("kill-window", "-t", found[0], check=False)


def capture(name: str, lines: int = 50) -> str:
    found = _find(name)
    if not found:
        raise TmuxError(f"no tmux window '{name}'")
    p = _run("capture-pane", "-p", "-t", found[0], "-S", f"-{lines}")
    return p.stdout


def send_keys(name: str, *keys: str) -> None:
    found = _find(name)
    if found:
        _run("send-keys", "-t", found[0], *keys, check=False)


def pane_pids() -> list[int]:
    """PIDs of the panes' root processes in the cm session."""
    if not _has_session():
        return []
    p = _run("list-panes", "-s", "-t", f"={TMUX_SESSION}", "-F", "#{pane_pid}", check=False)
    return [int(x) for x in p.stdout.split() if x.isdigit()]
