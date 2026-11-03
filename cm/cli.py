"""`cm` command line interface."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid

from . import claude_store, config, daemon, procs, state, tmux

MODES = ["acceptEdits", "auto", "bypassPermissions", "default", "dontAsk", "plan"]


class CliError(Exception):
    pass


def _ts(t) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "-"


def _table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(len(str(x)) for x in col) for col in zip(headers, *rows)] if rows else [len(h) for h in headers]
    for r in [headers, *rows]:
        print("  ".join(str(c).ljust(w) for c, w in zip(r, widths)).rstrip())


def cmd_sessions(a) -> None:
    items = claude_store.list_sessions(a.dir, a.limit)
    if a.json:
        print(json.dumps([s.to_dict() for s in items], ensure_ascii=False, indent=2))
        return
    _table(["ID", "CWD", "MTIME", "TITLE"],
           [[s.id, s.cwd or "?", _ts(s.mtime), s.title] for s in items])


def cmd_ls(a) -> None:
    sessions = state.load()
    rows = []
    for s in sessions:
        alive = tmux.is_alive(state.short_id(s["id"]))
        rows.append({**s, "alive": alive})
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    _table(["NAME", "ID", "CWD", "STATUS", "RESTARTS"],
           [[r["name"], r["id"], r["cwd"], "alive" if r["alive"] else "dead", str(r.get("restarts", 0))]
            for r in rows])


def _check_mode(mode):
    if mode and mode not in MODES:
        raise CliError(f"invalid mode '{mode}', choose from: {', '.join(MODES)}")


def clone_command(url: str, dest: str) -> list[str]:
    """`gh repo clone` for owner/repo when gh exists, else `git clone`."""
    if re.fullmatch(r"[\w.-]+/[\w.-]+", url) and shutil.which("gh"):
        return ["gh", "repo", "clone", url, dest]
    return ["git", "clone", url, dest]


def cmd_start(a) -> None:
    _check_mode(a.mode)
    cwd = os.path.abspath(os.path.expanduser(a.dir))
    if a.clone and not os.path.exists(cwd):
        os.makedirs(os.path.dirname(cwd), exist_ok=True)
        if subprocess.run(clone_command(a.clone, cwd)).returncode != 0:
            raise CliError(f"clone of {a.clone} failed")
    elif a.create:
        os.makedirs(cwd, exist_ok=True)
    if not os.path.isdir(cwd):
        raise CliError(f"directory does not exist: {cwd} (use --create or --clone)")
    entry = state.new_entry(str(uuid.uuid4()), cwd, a.name or os.path.basename(cwd.rstrip("/")) or "root", a.mode)
    state.add(entry)
    _launch_or_rollback(entry)
    print(f"started {entry['name']} ({entry['id']}) in {cwd}")


def _launch_or_rollback(entry) -> None:
    try:
        daemon.launch(entry)
    except tmux.TmuxError as e:
        state.remove(entry["id"])
        raise CliError(str(e))


def cmd_resume(a) -> None:
    _check_mode(a.mode)
    if a.fork and a.force:
        raise CliError("--fork and --force are mutually exclusive")
    try:
        saved = claude_store.find_by_prefix(a.id)
    except LookupError as e:
        raise CliError(str(e))
    if not saved.cwd or not os.path.isdir(saved.cwd):
        raise CliError(f"cwd of session {saved.id} is unknown or missing: {saved.cwd}")
    name = a.name or os.path.basename(saved.cwd.rstrip("/")) or "root"
    try:
        action = procs.check_conflict(saved.id, a.fork, a.force)
    except procs.ConflictError as e:
        raise CliError(str(e))
    if action == "fork":
        entry = state.new_entry(str(uuid.uuid4()), saved.cwd, name, a.mode)
        entry["fork_from"] = saved.id
    else:
        entry = state.new_entry(saved.id, saved.cwd, name, a.mode)
    state.add(entry)
    _launch_or_rollback(entry)
    print(f"{'forked' if action == 'fork' else 'resumed'} {name} ({entry['id']}) in {saved.cwd}")


def _find(key: str) -> dict:
    return state.find(state.load(), key)


def _refuse_control(s: dict) -> None:
    if s["id"] == config.control_id():
        raise CliError("refusing to stop the control session 'manager'")


def cmd_stop(a) -> None:
    s = _find(a.session)
    _refuse_control(s)
    tmux.kill(state.short_id(s["id"]))
    state.remove(s["id"])
    print(f"stopped {s['name']} ({s['id']})")


def cmd_restart(a) -> None:
    s = _find(a.session)
    daemon.launch(s)
    state.update(s["id"], restarts=s.get("restarts", 0) + 1, last_restart=time.time())
    print(f"restarted {s['name']} ({s['id']})")


def _run_git(*args: str) -> str:
    p = subprocess.run(["git", "-C", str(config.REPO_ROOT), *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise CliError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout


def cmd_update(a) -> None:
    pyproject = config.REPO_ROOT / "pyproject.toml"
    before = pyproject.read_bytes() if pyproject.exists() else b""
    print(_run_git("pull", "--ff-only").strip())
    after = pyproject.read_bytes() if pyproject.exists() else b""
    if before != after:
        print("pyproject.toml changed, reinstalling...")
        r = subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "-e", str(config.REPO_ROOT)])
        if r.returncode != 0:
            raise CliError("pip install failed")
    # Restart only the daemon service; tmux sessions keep running (the service
    # units are configured not to kill the tmux server on stop).
    if sys.platform == "darwin":
        cmd = ["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/com.claude-manager"]
    else:
        cmd = ["systemctl", "--user", "restart", "claude-manager"]
    if subprocess.run(cmd).returncode != 0:
        raise CliError("could not restart the daemon service: " + " ".join(cmd))
    print("daemon restarted; tmux sessions untouched")


def cmd_logs(a) -> None:
    s = _find(a.session)
    print(tmux.capture(state.short_id(s["id"]), a.n), end="")


def cmd_daemon(a) -> None:
    daemon.run(once=a.once, interval=a.interval)


def cmd_control(a) -> None:
    s = daemon.ensure_control()
    print(f"control session registered: {s['name']} ({s['id']}) in {s['cwd']}")
    print("the daemon will start it; or run `cm daemon --once`")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cm", description="Claude Code Remote Control session manager")
    sub = p.add_subparsers(dest="cmd", required=True)

    x = sub.add_parser("sessions", help="list saved Claude sessions")
    x.add_argument("--dir")
    x.add_argument("--limit", type=int, default=20)
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_sessions)

    x = sub.add_parser("ls", help="list managed sessions")
    x.add_argument("--json", action="store_true")
    x.set_defaults(fn=cmd_ls)

    x = sub.add_parser("start", help="start a new managed session in a directory")
    x.add_argument("dir")
    x.add_argument("--name")
    x.add_argument("--mode", help="permission mode (default from config, 'auto'): " + ", ".join(MODES))
    x.add_argument("--create", action="store_true", help="mkdir -p the directory")
    x.add_argument("--clone", metavar="URL", help="clone git URL (or owner/repo via gh) into dir if absent")
    x.set_defaults(fn=cmd_start)

    x = sub.add_parser("resume", help="manage an existing saved session")
    x.add_argument("id")
    x.add_argument("--name")
    x.add_argument("--mode")
    x.add_argument("--fork", action="store_true", help="continue as a new forked session (new id)")
    x.add_argument("--force", action="store_true",
                   help="terminate a plain (non remote-control) claude process holding the session")
    x.set_defaults(fn=cmd_resume)

    for n, fn, h in (("stop", cmd_stop, "stop and forget a session"),
                     ("restart", cmd_restart, "restart a session")):
        x = sub.add_parser(n, help=h)
        x.add_argument("session", help="id, id prefix or name")
        x.set_defaults(fn=fn)

    x = sub.add_parser("logs", help="show recent terminal output of a session")
    x.add_argument("session")
    x.add_argument("-n", type=int, default=50)
    x.set_defaults(fn=cmd_logs)

    x = sub.add_parser("daemon", help="run the keep-alive loop")
    x.add_argument("--once", action="store_true")
    x.add_argument("--interval", type=int, default=30)
    x.set_defaults(fn=cmd_daemon)

    x = sub.add_parser("update", help="git pull, reinstall if needed, restart the daemon service")
    x.set_defaults(fn=cmd_update)

    x = sub.add_parser("control", help="register the control session")
    x.set_defaults(fn=cmd_control)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (CliError, state.StateError, tmux.TmuxError) as e:
        print(f"cm: error: {e}", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
