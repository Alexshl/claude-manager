#!/usr/bin/env bash
# claude-manager installer (idempotent). macOS (launchd) and Linux (systemd --user).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS="$(uname -s)"
VENV="$HOME/.local/share/claude-manager/venv"
BIN_DIR="$HOME/.local/bin"
CM_BIN="$BIN_DIR/cm"

if ! command -v claude >/dev/null 2>&1; then
  echo "claude не знайдено в PATH. Встановіть Claude Code: https://docs.claude.com/claude-code" >&2
  exit 1
fi

if ! command -v tmux >/dev/null 2>&1; then
  case "$OS" in
    Darwin)
      if command -v brew >/dev/null 2>&1; then brew install tmux
      else echo "Встановіть tmux (потрібен Homebrew: brew install tmux)" >&2; exit 1; fi ;;
    Linux)
      echo "tmux не знайдено. Встановіть: sudo apt install tmux  (або: sudo dnf install tmux)" >&2; exit 1 ;;
    *) echo "Непідтримувана ОС: $OS" >&2; exit 1 ;;
  esac
fi

command -v python3 >/dev/null 2>&1 || { echo "python3 не знайдено" >&2; exit 1; }
python3 -c 'import sys; sys.exit(sys.version_info < (3,10))' || { echo "Потрібен Python 3.10+" >&2; exit 1; }

mkdir -p "$(dirname "$VENV")" "$BIN_DIR" "$HOME/.local/state/claude-manager"
[ -x "$VENV/bin/python" ] || python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet -e "$REPO"
ln -sf "$VENV/bin/cm" "$CM_BIN"

# PATH for the service: dirs of claude/tmux/python plus the current PATH, so claude is found.
SVC_PATH="$(dirname "$(command -v claude)"):$(dirname "$(command -v tmux)"):$BIN_DIR:$PATH"

render() { # template -> stdout
  sed -e "s|@CM_BIN@|$CM_BIN|g" -e "s|@PATH@|$SVC_PATH|g" -e "s|@HOME@|$HOME|g" "$1"
}

case "$OS" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.claude-manager.plist"
    mkdir -p "$(dirname "$PLIST")"
    render "$REPO/cm/service/com.claude-manager.plist.tmpl" > "$PLIST"
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
    ;;
  Linux)
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    render "$REPO/cm/service/claude-manager.service.tmpl" > "$UNIT_DIR/claude-manager.service"
    systemctl --user daemon-reload
    systemctl --user enable --now claude-manager.service
    loginctl enable-linger "$USER" 2>/dev/null || echo "(не вдалося loginctl enable-linger — зробіть вручну, щоб сервіс жив без логіну)"
    ;;
  *) echo "Непідтримувана ОС: $OS" >&2; exit 1 ;;
esac

"$CM_BIN" control

echo
echo "Готово. Якщо ще не логінились — запустіть один раз інтерактивно: claude"
echo "(без входу Remote Control не запрацює). Переконайтесь, що $BIN_DIR є у PATH."
