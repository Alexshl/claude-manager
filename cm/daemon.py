"""Keep-alive daemon and session launching."""
from __future__ import annotations

import logging
import os
import shlex
import signal
import time
from logging.handlers import RotatingFileHandler

from . import claude_store, config, state, tmux

INITIAL_DELAY = 10
MAX_DELAY = 600
HEALTHY_RESET = 600

log = logging.getLogger("cm")


def build_command(sess: dict) -> str:
    sid = sess["id"]
    label = config.display_name(config.CONTROL_NAME if sid == config.control_id() else sess["name"])
    if sess.get("fork_from") and not claude_store.session_exists(sid):
        # First launch of a fork: new id for the forked conversation.
        args = ["claude", "--resume", sess["fork_from"], "--fork-session", "--session-id", sid]
    elif claude_store.session_exists(sid):
        args = ["claude", "--resume", sid]
    else:
        args = ["claude", "--session-id", sid]
    args += ["--remote-control", label,
             "--permission-mode", sess.get("permission_mode") or config.default_permission_mode()]
    return shlex.join(args)


def launch(sess: dict) -> None:
    """(Re)start the tmux window for a managed session."""
    try:
        claude_store.trust_folder(sess["cwd"])
    except (OSError, ValueError) as e:
        log.warning("could not mark %s trusted: %s", sess["cwd"], e)
    name = state.short_id(sess["id"])
    tmux.start_window(name, sess["cwd"], build_command(sess))
    _accept_trust_prompt(name)
    if not sess.get("started"):
        state.update(sess["id"], started=time.time())


TRUST_PROMPT = "Yes, I trust this folder"
READY_MARK = "remote-control is active"


def _accept_trust_prompt(name: str, timeout: float = 30) -> None:
    """Answer the folder-trust dialog that still appears when the folder's
    .claude/settings.json pre-approves permissions (hasTrustDialogAccepted
    doesn't skip it). The user asked cm to run this folder, so we accept."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(1)
        try:
            screen = tmux.capture(name, 60)
        except tmux.TmuxError:
            return
        if READY_MARK in screen:
            return
        if TRUST_PROMPT in screen:
            log.info("accepting folder trust prompt in %s", name)
            tmux.send_keys(name, "Down")
            time.sleep(0.3)
            tmux.send_keys(name, "Enter")
            return


def ensure_control() -> dict:
    """Register the control session in state (id fixed in config)."""
    cid = config.control_id()
    with state.lock():
        sessions = state.load()
        for s in sessions:
            if s["id"] == cid:
                return s
        entry = state.new_entry(cid, str(config.CONTROL_DIR), config.CONTROL_NAME)
        sessions = [s for s in sessions if s.get("name") != config.CONTROL_NAME]
        sessions.append(entry)
        state.save(sessions)
        return entry


def _setup_logging() -> None:
    path = config.log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    if os.isatty(2):
        log.addHandler(logging.StreamHandler())


def tick(track: dict[str, dict]) -> None:
    """One pass. `track` holds per-session backoff data across passes."""
    try:
        ensure_control()
        sessions = state.load()
    except (OSError, state.StateError) as e:
        log.error("state error: %s", e)
        return
    now = time.time()
    ids = {s["id"] for s in sessions}
    for gone in [k for k in track if k not in ids]:
        del track[gone]
    for s in sessions:
        sid = s["id"]
        t = track.setdefault(sid, {"delay": INITIAL_DELAY, "next": 0.0, "started": None})
        name = state.short_id(sid)
        try:
            if tmux.is_alive(name):
                if t["started"] and now - t["started"] >= HEALTHY_RESET:
                    t["delay"] = INITIAL_DELAY
                continue
            if now < t["next"]:
                continue
            log.info("session %s (%s) is not alive, restarting", name, s.get("name"))
            first = not s.get("started")  # very first launch is not a "restart"
            launch(s)
            t["started"] = now
            t["next"] = now + t["delay"]
            t["delay"] = min(t["delay"] * 2, MAX_DELAY)
            if not first:
                state.update(sid, restarts=s.get("restarts", 0) + 1, last_restart=now)
        except Exception as e:  # keep daemon alive whatever happens
            log.error("restart of %s failed: %s", name, e)
            t["next"] = now + t["delay"]
            t["delay"] = min(t["delay"] * 2, MAX_DELAY)


def run(once: bool = False, interval: int = 30) -> None:
    _setup_logging()
    stop = {"flag": False}

    def _term(signum, frame):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    log.info("daemon started (interval %ss)", interval)
    track: dict[str, dict] = {}
    while not stop["flag"]:
        tick(track)
        if once:
            break
        deadline = time.time() + interval
        while not stop["flag"] and time.time() < deadline:
            time.sleep(min(1, max(0, deadline - time.time())))
    log.info("daemon stopped")
