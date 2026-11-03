"""Paths and global settings."""
from __future__ import annotations

import fcntl
import json
import os
import socket
import uuid
from pathlib import Path

TMUX_SESSION = "cm"
CONTROL_NAME = "manager"
REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_DIR = REPO_ROOT / "control"


def _xdg(var: str, default: str) -> Path:
    val = os.environ.get(var)
    return Path(val) if val else Path.home() / default


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / "claude-manager"


def state_dir() -> Path:
    return _xdg("XDG_STATE_HOME", ".local/state") / "claude-manager"


def state_path() -> Path:
    return config_dir() / "state.json"


def config_path() -> Path:
    return config_dir() / "config.json"


def log_path() -> Path:
    return state_dir() / "daemon.log"


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")


def claude_projects_dir() -> Path:
    return claude_home() / "projects"


def claude_json_path() -> Path:
    return Path.home() / ".claude.json"


def load() -> dict:
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def short_host() -> str:
    return socket.gethostname().split(".")[0] or "host"


def host_label() -> str:
    """Host part of remote session names: config `host_label`, else short hostname."""
    return load().get("host_label") or short_host()


def default_permission_mode() -> str:
    return load().get("default_permission_mode") or "auto"


def display_name(name: str) -> str:
    """Name shown in Claude Desktop: "<host>: <name>"."""
    return f"{host_label()}: {name}"


def control_id() -> str:
    """Fixed id of the control session; generated once and stored in config.json."""
    cid = load().get("control_id")
    if cid:
        return cid
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # The daemon and `cm control` can race here on first install: lock, then re-read.
    with open(path.parent / "config.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = load()
        cid = data.get("control_id")
        if not cid:
            cid = str(uuid.uuid4())
            data["control_id"] = cid
            tmp = path.with_name(f"{path.name}.tmp{os.getpid()}")
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, path)
    return cid
