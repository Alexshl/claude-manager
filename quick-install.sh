#!/usr/bin/env bash
# claude-manager quick installer.
#   curl -fsSL https://raw.githubusercontent.com/Alexshl/claude-manager/main/quick-install.sh | bash
# Options: --yes (or CM_YES=1) non-interactive; --dry-run (or CM_DRY_RUN=1) print actions only.
# Env: CM_DIR (checkout dir), CM_PYTHON (interpreter), CM_HOST_LABEL (host label).
set -euo pipefail

REPO_URL="https://github.com/Alexshl/claude-manager.git"
TOTAL=9

YES="${CM_YES:-0}"
DRY="${CM_DRY_RUN:-0}"
for arg in "$@"; do
  case "$arg" in
    --yes|-y) YES=1 ;;
    --dry-run) DRY=1 ;;
    -h|--help) sed -n '2,5p' "${BASH_SOURCE[0]:-/dev/null}" 2>/dev/null || true; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ -t 1 ]; then
  C_OK=$'\033[32m'; C_ERR=$'\033[31m'; C_WARN=$'\033[33m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_OK=""; C_ERR=""; C_WARN=""; C_BOLD=""; C_OFF=""
fi

STEP=0
step() { STEP=$((STEP + 1)); printf '\n%s[%d/%d] %s%s\n' "$C_BOLD" "$STEP" "$TOTAL" "$1" "$C_OFF"; }
ok()   { printf '  %s✓%s %s\n' "$C_OK" "$C_OFF" "$1"; }
bad()  { printf '  %s✗%s %s\n' "$C_ERR" "$C_OFF" "$1" >&2; }
warn() { printf '  %s!%s %s\n' "$C_WARN" "$C_OFF" "$1"; }
info() { printf '    %s\n' "$1"; }
die()  { bad "$1"; exit 1; }

# ask "question" Y|N  -> returns 0 for yes. With --yes the default answer is used.
ask() {
  local prompt="$1" def="${2:-Y}" hint reply
  if [ "$def" = "Y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
  if [ "$YES" = "1" ]; then
    printf '    %s %s -> %s (auto)\n' "$prompt" "$hint" "$def"
    [ "$def" = "Y" ]
    return
  fi
  if [ ! -r /dev/tty ]; then
    die "no terminal available for prompts; re-run with --yes (CM_YES=1)"
  fi
  printf '    %s %s ' "$prompt" "$hint" > /dev/tty
  read -r reply < /dev/tty || reply=""
  reply="${reply:-$def}"
  case "$reply" in [Yy]*) return 0 ;; *) return 1 ;; esac
}

# read a line from the terminal with a default
prompt_line() {
  local prompt="$1" def="$2" reply=""
  if [ "$YES" = "1" ] || [ ! -r /dev/tty ]; then
    printf '%s' "$def"
    return
  fi
  printf '    %s [%s]: ' "$prompt" "$def" > /dev/tty
  read -r reply < /dev/tty || reply=""
  printf '%s' "${reply:-$def}"
}

# run a command (array) or just print it in dry-run mode
run() {
  if [ "$DRY" = "1" ]; then
    printf '    [dry-run] %s\n' "$*"
    return 0
  fi
  "$@"
}

# run a shell snippet
run_sh() {
  if [ "$DRY" = "1" ]; then
    printf '    [dry-run] %s\n' "$1"
    return 0
  fi
  bash -c "$1"
}

has() { command -v "$1" >/dev/null 2>&1; }

OS=""; PM=""; SUDO=""
detect_os() {
  case "$(uname -s)" in
    Darwin) OS="macos"; PM="brew"; ok "macOS $(sw_vers -productVersion 2>/dev/null || true)" ;;
    Linux)
      OS="linux"
      local id="" like=""
      if [ -r /etc/os-release ]; then
        # shellcheck disable=SC1091
        id="$(. /etc/os-release; echo "${ID:-}")"
        # shellcheck disable=SC1091
        like="$(. /etc/os-release; echo "${ID_LIKE:-}")"
      fi
      case " $id $like " in
        *" debian "*|*" ubuntu "*) PM="apt" ;;
        *" fedora "*|*" rhel "*|*" centos "*) PM="dnf" ;;
        *" arch "*) PM="pacman" ;;
        *" suse "*|*" opensuse "*) PM="zypper" ;;
        *" alpine "*) PM="apk" ;;
        *) die "unsupported Linux distribution (ID=${id:-unknown}); supported: apt, dnf, pacman, zypper, apk based" ;;
      esac
      ok "Linux (${id:-unknown}), package manager: $PM"
      if [ "$(id -u)" != "0" ]; then SUDO="sudo"; fi
      ;;
    *) die "unsupported OS: $(uname -s)" ;;
  esac
}

CLAUDE_BIN=""
check_claude() {
  if has claude; then
    CLAUDE_BIN="$(command -v claude)"
  elif [ -x "$HOME/.local/bin/claude" ]; then
    CLAUDE_BIN="$HOME/.local/bin/claude"
  elif [ -x "$HOME/.claude/local/claude" ]; then
    CLAUDE_BIN="$HOME/.claude/local/claude"
  fi
  if [ -z "$CLAUDE_BIN" ]; then
    bad "Claude Code is not installed"
    info "Install it first, then re-run this script:"
    info "  curl -fsSL https://claude.ai/install.sh | bash"
    exit 1
  fi
  ok "Claude Code found: $CLAUDE_BIN"
  # install.sh needs `claude` on PATH
  PATH="$(dirname "$CLAUDE_BIN"):$PATH"; export PATH

  local logged_in=1
  if [ "$OS" = "linux" ]; then
    [ -f "$HOME/.claude/.credentials.json" ] || logged_in=0
  elif "$CLAUDE_BIN" auth --help 2>&1 | grep -q "status"; then
    "$CLAUDE_BIN" auth status >/dev/null 2>&1 || logged_in=0
  else
    warn "cannot verify login (no 'claude auth status'); assuming logged in"
  fi
  if [ "$logged_in" = "1" ]; then
    ok "Claude Code login looks fine"
  else
    warn "Claude Code does not look logged in"
    info "Run 'claude' once interactively and sign in (Remote Control needs it)."
    ask "Continue anyway?" N || die "aborted; log in and re-run"
  fi
}

PKG_PY=""
pkgs_for() { # tool -> package names for $PM
  case "$1:$PM" in
    git:*|tmux:*) echo "$1" ;;
    python:apt) echo "python3 python3-venv python3-pip" ;;
    python:dnf|python:zypper) echo "python3 python3-pip" ;;
    python:pacman) echo "python" ;;
    python:apk) echo "python3 py3-pip" ;;
    python:brew) echo "python" ;;
  esac
}

install_cmd() { # prints the install command for the given packages
  case "$PM" in
    brew)   echo "brew install $*" ;;
    apt)    echo "${SUDO:+$SUDO }apt-get install -y $*" ;;
    dnf)    echo "${SUDO:+$SUDO }dnf install -y $*" ;;
    pacman) echo "${SUDO:+$SUDO }pacman -S --noconfirm $*" ;;
    zypper) echo "${SUDO:+$SUDO }zypper install -y $*" ;;
    apk)    echo "${SUDO:+$SUDO }apk add $*" ;;
  esac
}

ensure_brew() {
  if ! has brew; then
    local b
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
    done
  fi
  has brew && return 0
  warn "Homebrew is not installed"
  local cmd='/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
  info "$cmd"
  ask "Install Homebrew now?" Y || die "Homebrew is required on macOS to install dependencies"
  run_sh "$cmd"
  if [ "$DRY" != "1" ]; then
    local b
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do
      if [ -x "$b" ]; then eval "$("$b" shellenv)"; break; fi
    done
    has brew || die "Homebrew install finished but brew is not on PATH"
  fi
}

install_pkgs() { # tool-label, packages...
  local label="$1"; shift
  local cmd
  cmd="$(install_cmd "$@")"
  warn "$label is missing"
  info "$cmd"
  ask "Install now?" Y || die "$label is required"
  if [ "$PM" = "brew" ]; then ensure_brew; fi
  run_sh "$cmd"
}

py_ok() { # interpreter has Python >= 3.10 with venv+ensurepip
  has "$1" && "$1" -c 'import sys, venv, ensurepip; sys.exit(sys.version_info < (3, 10))' >/dev/null 2>&1
}

PYTHON=""
check_deps() {
  local tool
  for tool in git tmux; do
    if has "$tool"; then
      ok "$tool: $(command -v "$tool")"
    else
      # shellcheck disable=SC2046
      install_pkgs "$tool" $(pkgs_for "$tool")
      if [ "$DRY" != "1" ] && ! has "$tool"; then die "$tool is still missing after install"; fi
    fi
  done

  local cand
  for cand in "${CM_PYTHON:-}" python3; do
    [ -n "$cand" ] || continue
    if py_ok "$cand"; then PYTHON="$cand"; break; fi
  done
  if [ -z "$PYTHON" ]; then
    # shellcheck disable=SC2046
    install_pkgs "python3 >= 3.10 (with venv)" $(pkgs_for python)
    if [ "$OS" = "macos" ]; then
      PYTHON="$(brew --prefix 2>/dev/null || echo /opt/homebrew)/bin/python3"
    else
      PYTHON="python3"
    fi
    if [ "$DRY" != "1" ] && ! py_ok "$PYTHON"; then
      die "python >= 3.10 with venv is still unavailable ($PYTHON)"
    fi
  fi
  ok "python: $PYTHON"
}

CM_DIR="${CM_DIR:-$HOME/.local/share/claude-manager/src}"
fetch_repo() {
  if [ -d "$CM_DIR/.git" ]; then
    run git -C "$CM_DIR" pull --ff-only
    ok "updated $CM_DIR"
  elif [ -e "$CM_DIR" ] && [ -n "$(ls -A "$CM_DIR" 2>/dev/null)" ]; then
    die "$CM_DIR exists and is not a claude-manager checkout"
  else
    run mkdir -p "$(dirname "$CM_DIR")"
    run git clone "$REPO_URL" "$CM_DIR"
    ok "cloned into $CM_DIR"
  fi
}

set_host_label() {
  local def label cfg_dir
  def="${CM_HOST_LABEL:-$(hostname -s 2>/dev/null || hostname | cut -d. -f1)}"
  label="$(prompt_line "Host label shown in Claude Desktop" "$def")"
  cfg_dir="${XDG_CONFIG_HOME:-$HOME/.config}/claude-manager"
  if [ "$DRY" = "1" ]; then
    printf '    [dry-run] write host_label=%s to %s/config.json\n' "$label" "$cfg_dir"
    return 0
  fi
  mkdir -p "$cfg_dir"
  "$PYTHON" - "$cfg_dir/config.json" "$label" <<'PYEOF'
import json, os, sys
path, label = sys.argv[1], sys.argv[2]
try:
    with open(path) as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        data = {}
except (OSError, ValueError):
    data = {}
data["host_label"] = label
tmp = path + ".tmp"
with open(tmp, "w") as fh:
    json.dump(data, fh, indent=2)
os.replace(tmp, path)
PYEOF
  ok "host label: $label"
}

run_installer() {
  if [ "$DRY" = "1" ]; then
    printf '    [dry-run] CM_PYTHON=%s bash %s/install.sh\n' "$PYTHON" "$CM_DIR"
    return 0
  fi
  CM_PYTHON="$PYTHON" bash "$CM_DIR/install.sh"
  ok "install.sh finished"
}

ensure_path() {
  case ":$PATH:" in
    *":$HOME/.local/bin:"*) ok "~/.local/bin is on PATH"; return 0 ;;
  esac
  warn "~/.local/bin is not on PATH"
  local rc="$HOME/.bashrc"
  case "${SHELL:-}" in *zsh) rc="$HOME/.zshrc" ;; esac
  local line='export PATH="$HOME/.local/bin:$PATH"'
  if [ -f "$rc" ] && grep -qF "$line" "$rc"; then
    ok "$rc already exports it; open a new shell"
  elif ask "Append '$line' to $rc?" Y; then
    if [ "$DRY" = "1" ]; then
      printf '    [dry-run] append to %s\n' "$rc"
    else
      printf '\n# added by claude-manager\n%s\n' "$line" >> "$rc"
      ok "added to $rc (open a new shell)"
    fi
  fi
  PATH="$HOME/.local/bin:$PATH"; export PATH
}

platform_tips() {
  if [ "$OS" = "macos" ]; then
    info "For an unattended Mac:"
    info "- enable automatic login (System Settings > Users & Groups) so the launchd agent starts after reboot"
    info "- prevent sleep:  sudo pmset -a sleep 0 disksleep 0"
    if ask "Run 'sudo pmset -a sleep 0 disksleep 0' now?" N; then
      run sudo pmset -a sleep 0 disksleep 0
    fi
  else
    if has loginctl && loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q "yes"; then
      ok "systemd linger is enabled (service survives logout)"
    else
      warn "linger is not enabled; the service would stop when you log out"
      info "Fix: sudo loginctl enable-linger $USER"
    fi
  fi
}

final_check() {
  local host cfg label
  cfg="${XDG_CONFIG_HOME:-$HOME/.config}/claude-manager/config.json"
  host="$(hostname -s 2>/dev/null || hostname | cut -d. -f1)"
  label="$host"
  if [ -f "$cfg" ]; then
    label="$("$PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("host_label") or sys.argv[2])' "$cfg" "$host" 2>/dev/null || echo "$host")"
  fi
  if [ "$DRY" = "1" ]; then
    printf '    [dry-run] cm ls\n'
  else
    sleep 5
    "$HOME/.local/bin/cm" ls || true
  fi
  echo
  ok "Done. In Claude Desktop (Code tab) look for the session \"$label: manager\"."
  info "Ask it to start/stop/list sessions, or use the 'cm' command here."
}

main() {
  if [ "$DRY" = "1" ]; then printf '%s(dry run: nothing will be installed)%s\n' "$C_WARN" "$C_OFF"; fi
  step "Detect OS";            detect_os
  step "Claude Code";          check_claude
  step "Dependencies";         check_deps
  step "Fetch claude-manager"; fetch_repo
  step "Host label";           set_host_label
  step "Install";              run_installer
  step "PATH";                 ensure_path
  step "Platform tips";        platform_tips
  step "Verify";               final_check
}

main "$@"
