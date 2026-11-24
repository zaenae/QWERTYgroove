#!/usr/bin/env bash
# QWERTYgroove installer (macOS).
#   curl -fsSL https://raw.githubusercontent.com/zaenae/QWERTYgroove/main/install.sh | bash
set -uo pipefail

REPO="zaenae/QWERTYgroove"
BRANCH="main"
QG="$HOME/.qwertygroove"
APP_DIR="$QG/app"
VENV="$QG/venv"
UV="$QG/bin/uv"
PY="$VENV/bin/python"
BIN_DIR="$HOME/.local/bin"
LOG="$QG/install.log"
TOTAL=5
HELPER_DRIVER="/Library/Audio/Plug-Ins/HAL/BlackHole2ch.driver"

# Everything QWERTYgroove needs (its own Python included) lives in ~/.qwertygroove.
# No Apple developer tools or Homebrew needed.
export UV_PYTHON_INSTALL_DIR="$QG/python" UV_CACHE_DIR="$QG/cache/uv"

mkdir -p "$QG/cache"
: > "$LOG"

# ------------------------------------------------------------------ looks
if [ -t 1 ]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[0m'
  TEAL=$'\033[38;5;43m'; GREY=$'\033[38;5;245m'; GREEN=$'\033[38;5;78m'; RED=$'\033[38;5;203m'; YEL=$'\033[38;5;221m'
else
  B=""; D=""; R=""; TEAL=""; GREY=""; GREEN=""; RED=""; YEL=""
fi

banner() {
  local line="${GREY}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${R}"
  printf '\n  %s\n' "$line"
  printf '  %s━━━━━━━━━━%s  %sQ W E R T Y%s %sg r o o v e%s  %s━━━━━━━━━━━%s\n' "$GREY" "$R" "$B" "$R" "$TEAL" "$R" "$GREY" "$R"
  printf '  %s\n\n' "$line"
  printf '  %sYour MacBook keyboard light, grooving to your music.%s\n' "$B" "$R"
  printf '  %sMade for M-series MacBooks (M1 and newer) with a backlit keyboard.%s\n\n' "$D" "$R"
}

STEP=0
step() { STEP=$((STEP + 1)); printf '\n  %s%s/%s%s  %s%s%s\n' "$TEAL" "$STEP" "$TOTAL" "$R" "$B" "$1" "$R"; }
ok()   { printf '       %s✓%s %s\n' "$GREEN" "$R" "$1"; }
info() { printf '       %s%s%s\n' "$D" "$1" "$R"; }
warn() { printf '       %s!%s %s\n' "$YEL" "$R" "$1"; }

fail() {
  printf '\n  %s✗ %s%s\n\n' "$RED" "$1" "$R"
  [ $# -gt 1 ] && printf '    %s\n' "${@:2}" && echo
  printf '    %sThe details are saved in %s%s\n' "$D" "${LOG/#$HOME/~}" "$R"
  printf '    %sRun the same install command again to retry.%s\n\n' "$D" "$R"
  exit 1
}

# run "message" command...  -> spinner while it works, ✓ when done (details go to the log)
run() {
  local msg="$1"; shift
  echo "### $msg: $*" >> "$LOG"
  "$@" >> "$LOG" 2>&1 &
  local pid=$! i=0
  local frames=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)              # whole symbols, safe in any language setting
  if [ -t 1 ]; then
    printf '\033[?25l'
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r       %s%s%s %s' "$TEAL" "${frames[i++ % 10]}" "$R" "$msg"
      sleep 0.08
    done
    printf '\r\033[K\033[?25h'
  fi
  if wait "$pid"; then ok "$msg"; else return 1; fi
}

trap 'printf "\033[?25h"' EXIT

# ------------------------------------------------------------------ go
banner

# 1 — the Mac
step "Checking your Mac"
[ "$(uname -s)" = "Darwin" ] || fail "QWERTYgroove only works on a Mac."
if [ "$(uname -m)" = "arm64" ]; then ok "Looks good"; else warn "This looks like an older Intel Mac. QWERTYgroove is made for M1 and newer, so it may not work."; fi

# 2 — password, once
step "Your password"

# Reads the password showing a * for each letter (plain sudo shows nothing, which
# confuses people), then hands it straight to sudo to check. It isn't kept.
ask_password() {
  local pw="" ch
  printf '       %s🔑 Password:%s ' "$TEAL" "$R"
  while IFS= read -r -s -n 1 ch < /dev/tty; do
    case "$ch" in
      "") break ;;                                            # Return
      $'\x7f'|$'\b') if [ -n "$pw" ]; then pw="${pw%?}"; printf '\b \b'; fi ;;   # Backspace
      *) pw+="$ch"; printf '*' ;;
    esac
  done
  echo
  printf '%s\n' "$pw" | sudo -S -v -p '' >/dev/null 2>&1
  local result=$?
  pw=""
  return $result
}

if sudo -n true 2>/dev/null; then
  ok "Already unlocked"
else
  info "Type the password you use to unlock your Mac, then press Return."
  echo
  tries=0
  until ask_password; do
    tries=$((tries + 1))
    [ "$tries" -ge 3 ] && fail "That password didn't work." "Use the password you unlock your Mac with, then run the same command again."
    printf '       %s✗ That didn'"'"'t work. Try again.%s\n' "$RED" "$R"
  done
fi
( while true; do sudo -n true 2>/dev/null; sleep 50; kill -0 "$$" 2>/dev/null || exit; done ) &
ok "Thanks! Now sit back ☕"

# 3 — the app, with its own Python
step "Installing QWERTYgroove"
TMP="$(mktemp -d)"
LOCAL_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  LOCAL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  [ -d "$LOCAL_DIR/qwertygroove" ] || LOCAL_DIR=""
fi
if [ -n "$LOCAL_DIR" ]; then
  run "Copying the app" cp -R "$LOCAL_DIR/." "$TMP/" || fail "Couldn't copy the app."
else
  run "Downloading the app" bash -c "curl -fsSL 'https://github.com/$REPO/archive/refs/heads/$BRANCH.tar.gz' | tar -xz -C '$TMP' --strip-components=1" \
    || fail "Couldn't download QWERTYgroove." "Check your internet connection."
fi
rm -rf "$APP_DIR"
mv "$TMP" "$APP_DIR"

if [ ! -x "$UV" ]; then          # uv: a small, fast tool that sets up Python (astral.sh/uv)
  run "Getting the tools it needs" bash -c "curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR='$QG/bin' UV_NO_MODIFY_PATH=1 INSTALLER_NO_MODIFY_PATH=1 sh" \
    || fail "Couldn't download what's needed." "Check your internet connection and try again."
fi
run "Getting Python ready" "$UV" venv --managed-python --python 3.12 --clear --quiet "$VENV" \
  || fail "Couldn't set up Python." "Check your internet connection and try again."
run "Adding what it needs" "$UV" pip install --python "$PY" --quiet \
    --only-binary numpy --only-binary pyobjc-core --only-binary sounddevice \
    numpy sounddevice pyobjc-core \
  || fail "Couldn't install what the app needs." "Check your internet connection and try again."
if ! run "Setting up its window" "$UV" pip install --python "$PY" --quiet pywebview; then
  warn "It'll open in your web browser instead of its own window. Works just the same."
fi

# 4 — the sound helper, so the app can hear your music
step "Setting up sound"
helper_ready() {
  PYTHONPATH="$APP_DIR" "$PY" -c 'import sys
from qwertygroove import coreaudio
sys.exit(0 if any("blackhole" in n.lower() for _, n, _ in coreaudio.outputs()) else 1)' 2>/dev/null
}

install_helper() {   # BlackHole (free, open source), straight from its makers: checked before installing
  local meta url sum pkg="$QG/cache/sound-helper.pkg"
  meta="$(curl -fsSL https://formulae.brew.sh/api/cask/blackhole-2ch.json)" || return 1
  url="$(printf '%s' "$meta" | "$PY" -c 'import sys, json; print(json.load(sys.stdin)["url"])')" || return 1
  sum="$(printf '%s' "$meta" | "$PY" -c 'import sys, json; print(json.load(sys.stdin)["sha256"])')" || return 1
  case "$url" in https://*) ;; *) echo "unexpected download address: $url"; return 1 ;; esac
  echo "downloading $url"
  curl -fsSL -o "$pkg" "$url" || return 1
  if [ "$sum" != "no_check" ]; then
    [ "$("$PY" -c 'import hashlib, sys; print(hashlib.sha256(open(sys.argv[1], "rb").read()).hexdigest())' "$pkg")" = "$sum" ] \
      || { echo "download doesn't match its checksum"; rm -f "$pkg"; return 1; }
  fi
  pkgutil --check-signature "$pkg" | grep -q "Existential Audio" \
    || { echo "installer isn't signed by Existential Audio"; rm -f "$pkg"; return 1; }
  sudo -n /usr/sbin/installer -pkg "$pkg" -target / || return 1
  rm -f "$pkg"
}

# macOS only picks up a new sound helper after its sound service restarts.
# Restarting just that service (a second of silence) avoids restarting the Mac.
switch_on() {
  helper_ready && return 0
  sudo -n killall coreaudiod 2>/dev/null || sudo -n launchctl kickstart -k system/com.apple.audio.coreaudiod 2>/dev/null
  for _ in $(seq 1 20); do sleep 0.5; helper_ready && return 0; done
  return 1
}

if [ -d "$HELPER_DRIVER" ]; then
  ok "The sound helper is already installed"
else
  run "Installing the sound helper" install_helper \
    || fail "Couldn't set up sound." "Check your internet connection and run the same command again."
fi
if ! run "Switching it on" switch_on; then
  info "It'll switch on the first time you play your music."
fi

# 5 — the command
step "Finishing up"
mkdir -p "$BIN_DIR"
printf '#!/bin/sh\nexport PYTHONPATH="%s${PYTHONPATH:+:$PYTHONPATH}"\nexec "%s" -m qwertygroove "$@"\n' \
  "$APP_DIR" "$PY" > "$BIN_DIR/qwertygroove"
chmod +x "$BIN_DIR/qwertygroove"
grep -qs '.local/bin' "$HOME/.zprofile" || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.zprofile"
ok "Added the ${B}qwertygroove${R} command"

# ------------------------------------------------------------------ done
box() {
  printf '\n  %s┌────────────────────────────────────────────────────┐%s\n' "$GREY" "$R"
  for l in "$@"; do
    local plain; plain="$(printf '%s' "$l" | sed $'s/\033\\[[0-9;]*m//g')"
    printf '  %s│%s  %s%*s%s│%s\n' "$GREY" "$R" "$l" $((50 - ${#plain})) "" "$GREY" "$R"
  done
  printf '  %s└────────────────────────────────────────────────────┘%s\n\n' "$GREY" "$R"
}

printf '\n  %s%s✓ All done!%s\n' "$GREEN" "$B" "$R"
box "Whenever you want to groove, open Terminal" \
    "and type:" \
    "" \
    "    ${TEAL}${B}qwertygroove${R}"
if [ -r /dev/tty ] && [ -t 1 ]; then
  printf '  %s▶ Press %sY%s%s on your keyboard to open QWERTYgroove now%s ' "$B" "$TEAL" "$R" "$B" "$R"
  printf '%s(any other key to skip)%s ' "$D" "$R"
  read -r -s -n 1 reply < /dev/tty || reply="n"          # one key, no Return needed
  echo
  if [ -z "$reply" ] || [[ "$reply" =~ ^[Yy] ]]; then
    printf '\n  %sEnjoy! 🎶%s\n\n' "$TEAL" "$R"
    exec "$BIN_DIR/qwertygroove"
  fi
fi
