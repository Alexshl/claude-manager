#!/usr/bin/env bash
# Reverses install.sh. State is kept unless --purge is given.
set -uo pipefail

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1
OS="$(uname -s)"

case "$OS" in
  Darwin)
    PLIST="$HOME/Library/LaunchAgents/com.claude-manager.plist"
    launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    ;;
  Linux)
    systemctl --user disable --now claude-manager.service 2>/dev/null || true
    rm -f "$HOME/.config/systemd/user/claude-manager.service"
    systemctl --user daemon-reload 2>/dev/null || true
    ;;
esac

command -v tmux >/dev/null 2>&1 && tmux kill-session -t =cm 2>/dev/null || true
rm -f "$HOME/.local/bin/cm"
rm -rf "$HOME/.local/share/claude-manager"

if [ "$PURGE" = 1 ]; then
  rm -rf "${XDG_CONFIG_HOME:-$HOME/.config}/claude-manager" "${XDG_STATE_HOME:-$HOME/.local/state}/claude-manager"
  echo "Стан і логи видалено."
else
  echo "Стан (~/.config/claude-manager) збережено. Для повного видалення: ./uninstall.sh --purge"
fi
echo "Видалено."
