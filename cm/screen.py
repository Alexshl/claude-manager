"""Pure classification of a Claude Code terminal screen (no tmux, no I/O)."""
from __future__ import annotations

import re

from . import daemon

# Specific markers of permission prompts and startup dialogs. Any hit beats
# AskUserQuestion detection: our Enter there would approve something.
PERMISSION_MARKS = (
    "Do you want to proceed?", "Do you want to make this edit", "Do you want to create",
    "❯ 1. Yes", "1. Yes", "Yes, and don't ask again",
    "No, and tell Claude what to do differently",
)
AUQ_MARKS = ("Type something.", "Chat about this", "Enter to select", "↑/↓ to navigate")
SCREEN_LINES = 40

_OPT_RE = re.compile(r"^\s*(❯\s*)?(\d+)\.\s+(.+?)\s*$")
_CHECKBOX_RE = re.compile(r"^(\[[ xX✔✓]\]|[☐☒☑])(\s|$)")
_TAB_GLYPHS = "☐☒✔"


def tail(screen: str, lines: int = SCREEN_LINES) -> str:
    return "\n".join(screen.splitlines()[-lines:])


def _permission(screen: str) -> bool:
    return (daemon.DIALOG_MARK in screen or any(t in screen for t in daemon.STARTUP_PROMPTS)
            or any(t in screen for t in PERMISSION_MARKS))


def classify(screen: str) -> str:
    """'permission' (anything where Enter approves), 'auq' (AskUserQuestion widget) or 'other'."""
    screen = tail(screen)
    if _permission(screen):
        return "permission"
    if all(m in screen for m in AUQ_MARKS):
        return "auq"
    return "other"


def option_line(screen: str, n: int) -> tuple[bool, str] | None:
    """(has cursor, label) of the numbered line `n` in the widget, or None."""
    found = None
    for line in tail(screen).splitlines():
        m = _OPT_RE.match(line)
        if m and int(m.group(2)) == n:
            found = (bool(m.group(1)), m.group(3))
    return found


def parse_auq(screen: str) -> dict | None:
    """Parse a single-question AUQ widget.

    Returns {current, options: [(n, label)], type_n, multi_tab, multi_select}
    or None when the numbered list cannot be read. `options` excludes
    "Type something." and "Chat about this".
    """
    lines = tail(screen).splitlines()
    foot = next((i for i in range(len(lines) - 1, -1, -1) if "Enter to select" in lines[i]), None)
    typ = next((i for i in range(len(lines) - 1, -1, -1) if "Type something." in lines[i]), None)
    if foot is None or typ is None or typ > foot:
        return None
    tm = _OPT_RE.match(lines[typ])
    if not tm:
        return None
    type_n = int(tm.group(2))
    current = type_n if tm.group(1) else None
    options: list[tuple[int, str]] = []
    want = type_n - 1
    top = typ
    for i in range(typ - 1, -1, -1):
        m = _OPT_RE.match(lines[i])
        if m and int(m.group(2)) == want:
            options.append((want, m.group(3)))
            if m.group(1):
                current = want
            want -= 1
            top = i
            if want == 0:
                break
    if want != 0:
        return None
    options.reverse()
    for line in lines[typ + 1:foot]:
        m = _OPT_RE.match(line)
        if m and m.group(1):
            current = int(m.group(2))
    if current is None:
        return None
    multi_select = any(_CHECKBOX_RE.match(label) for _, label in options)
    head = [ln for ln in lines[:top] if not _OPT_RE.match(ln)]
    multi_tab = any(sum(ln.count(g) for g in _TAB_GLYPHS) >= 2
                    or ("Submit" in ln and any(g in ln for g in _TAB_GLYPHS)) for ln in head)
    return {"current": current, "options": options, "type_n": type_n,
            "multi_tab": multi_tab, "multi_select": multi_select}


def widget_open(screen: str) -> bool:
    """Markers of the AUQ widget still on screen (also while text is typed into it)."""
    s = tail(screen)
    return "Chat about this" in s or "Enter to select" in s
