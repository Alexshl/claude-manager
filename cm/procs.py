"""Detect running `claude` processes for a session id, and decide how to resume."""
from __future__ import annotations

import os
import signal
import subprocess
import time
from dataclasses import dataclass

from . import tmux


class ConflictError(Exception):
    pass


@dataclass
class Proc:
    pid: int
    ppid: int
    args: str

    @property
    def remote_control(self) -> bool:
        return "remote-control" in self.args


def parse_ps(text: str) -> list[Proc]:
    """Parse `ps -axo pid,ppid,args` output."""
    out = []
    for line in text.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            out.append(Proc(int(parts[0]), int(parts[1]), parts[2]))
    return out


def ps_all() -> list[Proc]:
    p = subprocess.run(["ps", "-axo", "pid,ppid,args"], capture_output=True, text=True)
    return parse_ps(p.stdout)


def _is_claude(args: str) -> bool:
    first = args.split(None, 1)[0] if args.split() else ""
    return os.path.basename(first) == "claude" or "/claude" in args.split(" --", 1)[0]


def find_session_procs(session_id: str, procs: list[Proc]) -> list[Proc]:
    me = os.getpid()
    return [p for p in procs if p.pid != me and session_id in p.args and _is_claude(p.args)]


def split_ours(found: list[Proc], procs: list[Proc], pane_pids: list[int]) -> tuple[list[Proc], list[Proc]]:
    """(ours, foreign): ours = descends from one of our tmux panes."""
    parent = {p.pid: p.ppid for p in procs}
    panes = set(pane_pids)

    def inside(pid: int) -> bool:
        seen = set()
        while pid and pid not in seen:
            if pid in panes:
                return True
            seen.add(pid)
            pid = parent.get(pid, 0)
        return False

    ours = [p for p in found if inside(p.pid)]
    return ours, [p for p in found if p not in ours]


def decide(foreign: list[Proc], fork: bool = False, force: bool = False) -> str:
    """Return 'launch', 'fork' or 'kill'; raise ConflictError to refuse."""
    if fork:
        return "fork"
    if not foreign:
        return "launch"
    pids = ", ".join(str(p.pid) for p in foreign)
    if any(p.remote_control for p in foreign):
        raise ConflictError(
            f"session is already running with Remote Control (pid {pids}); "
            "refusing even with --force. Use --fork to continue it as a new session.")
    if force:
        return "kill"
    raise ConflictError(
        f"session is already open in another claude process (pid {pids}). "
        "Close it, or use --fork (new session id) or --force (terminate that process).")


def check_conflict(session_id: str, fork: bool, force: bool) -> str:
    procs = ps_all()
    found = find_session_procs(session_id, procs)
    try:
        panes = tmux.pane_pids()
    except tmux.TmuxError:
        panes = []
    _, foreign = split_ours(found, procs, panes)
    action = decide(foreign, fork, force)
    if action == "kill":
        terminate(foreign)
    return action


def terminate(procs: list[Proc], wait: float = 5.0) -> None:
    for p in procs:
        try:
            os.kill(p.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.time() + wait
    while time.time() < deadline and any(_alive(p.pid) for p in procs):
        time.sleep(0.2)
    for p in procs:
        if _alive(p.pid):
            os.kill(p.pid, signal.SIGKILL)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
