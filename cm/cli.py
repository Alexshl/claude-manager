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
    def __init__(self, msg: str = "", code: int = 1):
        super().__init__(msg)
        self.code = code


EXIT_NOT_MANAGED = 3
EXIT_NOT_READY = 4
# Pane-text markers of prompts where our Enter would approve something.
PERMISSION_MARKS = (
    "Do you want to proceed?", "Do you want to make this edit", "Do you want to create",
    "❯ 1. Yes", "1. Yes", "Yes, and don't ask again",
    "No, and tell Claude what to do differently", "Esc to cancel",
)
MAX_SEND_CHARS = 16000


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
        rows.append({**s, "session_id": s["id"], "window": state.short_id(s["id"]),
                     "control": s["id"] == config.control_id(), "alive": alive})
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
        raise CliError("refusing to touch the control session 'manager'")


def cmd_stop(a) -> None:
    s = _find(a.session)
    _refuse_control(s)
    tmux.kill(state.short_id(s["id"]))
    state.remove(s["id"])
    print(f"stopped {s['name']} ({s['id']})")


def cmd_restart(a) -> None:
    s = _find(a.session)
    _refuse_control(s)
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


_CTRL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").rstrip()
    if not text.strip():
        raise CliError("empty text")
    if not text:
        raise CliError("empty text")
    if len(text) > MAX_SEND_CHARS:
        raise CliError(f"text is {len(text)} chars, limit is {MAX_SEND_CHARS}")
    if _CTRL_RE.search(text):
        raise CliError("text contains control characters (only newline and tab are allowed)")
    return text


def _dialog_on_screen(window: str) -> bool:
    screen = "\n".join(tmux.capture(window, 25).splitlines()[-25:])
    return (daemon.DIALOG_MARK in screen or any(t in screen for t in daemon.STARTUP_PROMPTS)
            or any(t in screen for t in PERMISSION_MARKS))


def _own_procs(session_id: str) -> list:
    allp = procs.ps_all()
    found = procs.find_session_procs(session_id, allp)
    try:
        panes = tmux.window_pane_pids(state.short_id(session_id))
    except tmux.TmuxError:
        panes = []
    return procs.split_ours(found, allp, panes)[0]


def cmd_send(a) -> None:
    raw = sys.stdin.buffer.read().decode("utf-8") if a.text == "-" else a.text
    text = _clean_text(raw)
    try:
        s = _find(a.session)
    except state.StateError as e:
        # ambiguous prefix is a plain error; "no match" means not managed
        raise CliError(str(e), 1 if "ambiguous" in str(e) else EXIT_NOT_MANAGED)
    if s["id"] == config.control_id():
        raise CliError("refusing to send to the control session 'manager'")
    win = state.short_id(s["id"])
    if not tmux.is_alive(win):
        raise CliError(f"session {s['name']} ({win}) is not alive; run `cm restart`", EXIT_NOT_MANAGED)
    if not _own_procs(s["id"]):
        raise CliError(f"no claude process of session {win} runs in its tmux window", EXIT_NOT_READY)
    deadline = time.time() + max(a.wait, 0)
    while _dialog_on_screen(win):
        if time.time() >= deadline:
            raise CliError(f"session {s['name']} ({win}) is waiting on a dialog; see `cm logs`", EXIT_NOT_READY)
        time.sleep(1)
    tmux.paste_and_submit(win, text)
    print(f"sent {len(text)} chars to {s['name']} ({win})")


def cmd_who(a) -> None:
    sessions = state.load()
    named = [s for s in sessions if s.get("name") == a.id]
    hits = named or [s for s in sessions if s["id"].startswith(a.id)]
    if len(hits) > 1:
        raise CliError(f"'{a.id}' is ambiguous: " + ", ".join(state.short_id(s["id"]) for s in hits))
    sid = hits[0]["id"] if hits else None
    if sid is None:
        try:
            sid = claude_store.find_by_prefix(a.id).id
        except LookupError:
            sid = a.id
    managed = next((s for s in sessions if s["id"] == sid), None)
    allp = procs.ps_all()
    found = procs.find_session_procs(sid, allp)
    try:
        panes = tmux.pane_pids()
    except tmux.TmuxError:
        panes = []
    ours, _ = procs.split_ours(found, allp, panes)
    ours_pids = {p.pid for p in ours}
    out = {
        "session_id": sid,
        "managed": managed is not None,
        "alive": bool(managed and tmux.is_alive(state.short_id(sid))),
        "holders": [{"pid": p.pid, "remote_control": p.remote_control, "ours": p.pid in ours_pids}
                    for p in found],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


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

    x = sub.add_parser("send", help="send a message to a managed session (bracketed paste + Enter)")
    x.add_argument("session", help="id, id prefix or name")
    x.add_argument("text", help="message, or - to read it from stdin")
    x.add_argument("--wait", type=int, default=0, metavar="N",
                   help="wait up to N seconds for a startup dialog to go away")
    x.set_defaults(fn=cmd_send)

    x = sub.add_parser("who", help="who holds a session: managed, alive, claude processes (JSON)")
    x.add_argument("id", help="session id, prefix or name")
    x.add_argument("--json", action="store_true", help="accepted for symmetry; output is always JSON")
    x.set_defaults(fn=cmd_who)

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
        return getattr(e, "code", 1)
    except UnicodeDecodeError:
        print("cm: error: stdin is not valid UTF-8", file=sys.stderr)
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
