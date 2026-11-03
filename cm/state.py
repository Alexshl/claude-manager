"""Desired-sessions state: atomic writes + file lock."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import time
from typing import Iterator

from . import config


class StateError(Exception):
    pass


@contextlib.contextmanager
def lock() -> Iterator[None]:
    path = config.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + ".lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def load() -> list[dict]:
    try:
        data = json.loads(config.state_path().read_text())
    except FileNotFoundError:
        return []
    except ValueError as e:
        raise StateError(f"state.json is corrupted: {e}")
    sessions = data.get("sessions", []) if isinstance(data, dict) else []
    return [s for s in sessions if isinstance(s, dict) and s.get("id")]


def save(sessions: list[dict]) -> None:
    path = config.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w") as fh:
        json.dump({"sessions": sessions}, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def new_entry(id: str, cwd: str, name: str, permission_mode: str | None = None) -> dict:
    return {
        "id": id,
        "cwd": cwd,
        "name": name,
        "permission_mode": permission_mode,
        "created": time.time(),
        "restarts": 0,
        "last_restart": None,
        "started": None,  # time of the first launch; None until launched
    }


def short_id(session_id: str) -> str:
    return session_id[:8]


def find(sessions: list[dict], key: str) -> dict:
    """Find by exact name, full id or id prefix. Raises StateError if none/ambiguous."""
    by_name = [s for s in sessions if s.get("name") == key]
    if len(by_name) == 1:
        return by_name[0]
    matches = [s for s in sessions if s["id"].startswith(key)]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise StateError(f"no managed session matches '{key}'")
    raise StateError(f"'{key}' is ambiguous: " + ", ".join(short_id(s["id"]) for s in matches))


def add(entry: dict) -> None:
    with lock():
        sessions = load()
        if any(s["id"] == entry["id"] for s in sessions):
            raise StateError(f"session {short_id(entry['id'])} is already managed")
        if any(s.get("name") == entry["name"] for s in sessions):
            raise StateError(f"name '{entry['name']}' is already used")
        sessions.append(entry)
        save(sessions)


def remove(session_id: str) -> None:
    with lock():
        save([s for s in load() if s["id"] != session_id])


def update(session_id: str, **fields) -> None:
    with lock():
        sessions = load()
        for s in sessions:
            if s["id"] == session_id:
                s.update(fields)
        save(sessions)
