"""Reading Claude Code saved sessions and ~/.claude.json trust settings."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path

from . import config

MAX_LINES = 50
TITLE_LEN = 80


@dataclass
class SavedSession:
    id: str
    cwd: str | None
    mtime: float
    title: str
    path: str

    def to_dict(self) -> dict:
        return asdict(self)


def _message_text(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str):
                return part["text"]
    return ""


def parse_session_file(path: Path) -> SavedSession | None:
    """Parse the first ~50 lines of a session jsonl. Robust to malformed lines."""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    cwd = None
    sid = None
    summary = None
    first_user = None
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= MAX_LINES:
                    break
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if cwd is None and isinstance(obj.get("cwd"), str):
                    cwd = obj["cwd"]
                if sid is None and isinstance(obj.get("sessionId"), str):
                    sid = obj["sessionId"]
                if obj.get("type") == "summary" and summary is None and isinstance(obj.get("summary"), str):
                    summary = obj["summary"]
                if obj.get("type") == "user" and first_user is None and not obj.get("isMeta"):
                    text = _message_text(obj.get("message")).strip()
                    # skip injected pseudo-messages like <command-name>, <local-command-...>
                    if text and not text.startswith("<"):
                        first_user = text
    except OSError:
        return None
    title = " ".join((summary or first_user or "").split())
    if len(title) > TITLE_LEN:
        title = title[: TITLE_LEN - 1] + "…"
    return SavedSession(id=path.stem, cwd=cwd, mtime=mtime, title=title, path=str(path))


def list_sessions(dir_filter: str | None = None, limit: int | None = None) -> list[SavedSession]:
    root = config.claude_projects_dir()
    files: list[tuple[float, Path]] = []
    try:
        for proj in root.iterdir():
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    files.append((f.stat().st_mtime, f))
                except OSError:
                    pass
    except OSError:
        return []
    files.sort(key=lambda t: t[0], reverse=True)
    want = os.path.realpath(os.path.expanduser(dir_filter)) if dir_filter else None
    out: list[SavedSession] = []
    for _, f in files:
        s = parse_session_file(f)
        if s is None:
            continue
        if want and (not s.cwd or os.path.realpath(s.cwd) != want):
            continue
        out.append(s)
        if limit and len(out) >= limit:
            break
    return out


def session_file(session_id: str) -> Path | None:
    """Path of the saved jsonl for an exact session id, or None."""
    try:
        for proj in config.claude_projects_dir().iterdir():
            f = proj / f"{session_id}.jsonl"
            if f.is_file():
                return f
    except OSError:
        pass
    return None


def session_exists(session_id: str) -> bool:
    return session_file(session_id) is not None


def find_by_prefix(prefix: str) -> SavedSession:
    matches: dict[str, Path] = {}
    try:
        for proj in config.claude_projects_dir().iterdir():
            for f in proj.glob(f"{prefix}*.jsonl"):
                matches[f.stem] = f
    except OSError:
        pass
    if not matches:
        raise LookupError(f"no saved session with id '{prefix}'")
    if len(matches) > 1:
        raise LookupError(f"id prefix '{prefix}' is ambiguous ({len(matches)} matches)")
    s = parse_session_file(next(iter(matches.values())))
    if s is None:
        raise LookupError(f"cannot read session '{prefix}'")
    return s


def trust_folder(path: str) -> None:
    """Mark a folder as trusted in ~/.claude.json to skip the interactive
    "do you trust this folder" dialog: projects[<abs path>].hasTrustDialogAccepted = true.
    Everything else in the file is preserved; backed up once to ~/.claude.json.cm-bak."""
    cj = config.claude_json_path()
    abs_path = os.path.abspath(path)
    try:
        data = json.loads(cj.read_text())
    except FileNotFoundError:
        data = {}
    if not isinstance(data, dict):
        raise ValueError(f"{cj} has unexpected structure")
    projects = data.setdefault("projects", {})
    entry = projects.setdefault(abs_path, {})
    if entry.get("hasTrustDialogAccepted") is True:
        return
    entry["hasTrustDialogAccepted"] = True
    bak = cj.with_name(cj.name + ".cm-bak")
    if cj.exists() and not bak.exists():
        shutil.copy2(cj, bak)
    tmp = cj.with_name(cj.name + f".cm-tmp{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
    try:
        os.chmod(tmp, cj.stat().st_mode & 0o777)
    except OSError:
        os.chmod(tmp, 0o600)
    os.replace(tmp, cj)
