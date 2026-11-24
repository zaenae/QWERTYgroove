#!/usr/bin/env bash
# QWERTYgroove installer (macOS).
#   curl -fsSL https://raw.githubusercontent.com/zaenae/QWERTYgroove/main/install.sh | bash
set -uo pipefail

REPO="zaenae/QWERTYgroove"
BRANCH="main"
APP_DIR="$HOME/.qwertygroove/app"
BIN_DIR="$HOME/.local/bin"
LOG="$HOME/.qwertygroove/install.log"
TOTAL=6

mkdir -p "$HOME/.qwertygroove"
: > "$LOG"
# Use the Python that comes with macOS (Homebrew's Python refuses --user installs)
if [ -x /usr/bin/python3 ]; then PY=/usr/bin/python3
else PY="$(command -v python3 || echo python3)"; fi

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

# 3 — the basics (Apple's free developer tools, then Homebrew)
step "Getting your Mac ready"
SWUPDATE=/usr/sbin/softwareupdate
tools_ready() { xcode-select -p >/dev/null 2>&1 && [ -x "$(xcode-select -p)/usr/bin/git" ]; }

install_tools_quietly() {         # same way Homebrew does it: Apple's updater, no pop-up
  local flag="/tmp/.com.apple.dt.CommandLineTools.installondemand.in-progress" label
  sudo -n touch "$flag"
  label="$("$SWUPDATE" -l 2>/dev/null | grep -B 1 -E 'Command Line Tools' \
          | awk -F'*' '/^ *\*/ {print $2}' | sed -e 's/^ *Label: //' -e 's/^ *//' | sort -V | tail -n1)"
  if [ -n "$label" ]; then
    sudo -n "$SWUPDATE" -i "$label" && sudo -n xcode-select --switch /Library/Developer/CommandLineTools
  fi
  sudo -n rm -f "$flag"
  tools_ready
}

wait_for_popup_install() {        # backup: Apple's pop-up, and we wait here until it's done
  for _ in $(seq 1 720); do tools_ready && return 0; sleep 5; done   # up to an hour
  return 1
}

if tools_ready; then
  ok "Apple's free tools are ready"
else
  info "Your Mac needs some free tools from Apple. This can take 5 to 10 minutes."
  if ! run "Installing Apple's free tools" install_tools_quietly; then
    xcode-select --install >/dev/null 2>&1 || true
    info "A window popped up. Click ${B}Install${R}${D}, then ${B}Agree${R}${D}. I'll wait right here."
    run "Waiting for Apple's installer to finish" wait_for_popup_install \
      || fail "Apple's tools didn't finish installing." "Run the same command again and it will pick up where it left off."
  fi
fi

BREW=""
for b in "$(command -v brew 2>/dev/null || true)" /opt/homebrew/bin/brew /usr/local/bin/brew; do
  if [ -n "$b" ] && [ -x "$b" ]; then BREW="$b"; break; fi
done
if [ -z "$BREW" ]; then
  BREW_SCRIPT="$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
    || fail "Couldn't download what's needed." "Check your internet connection and try again."
  run "Installing a few more free tools (a few minutes)" env NONINTERACTIVE=1 /bin/bash -c "$BREW_SCRIPT" \
    || fail "Couldn't get your Mac ready." "Run the same command again to retry."
  for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && BREW="$b" && break; done
  [ -n "$BREW" ] || fail "Couldn't get your Mac ready." "Run the same command again to retry."
else
  ok "Everything else is ready"
fi
eval "$("$BREW" shellenv)"

# 4 — audio helpers
step "Setting up sound"
run "Installing the sound helper" brew install --quiet blackhole-2ch switchaudio-osx \
  || fail "Couldn't set up sound."
# macOS only picks up the new sound helper after its sound service restarts.
# Restarting just that service (a second of silence) avoids restarting the Mac.
helper_ready() { SwitchAudioSource -a -t output 2>/dev/null | grep -qi blackhole; }
switch_on() {
  helper_ready && return 0
  sudo -n killall coreaudiod 2>/dev/null || sudo -n launchctl kickstart -k system/com.apple.audio.coreaudiod 2>/dev/null
  for _ in $(seq 1 20); do sleep 0.5; helper_ready && return 0; done
  return 1
}
if ! run "Switching it on" switch_on; then
  info "It'll switch on the first time you play your music."
fi

# 5 — the app
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
run "Adding what it needs" "$PY" -m pip install --user --quiet --disable-pip-version-check numpy sounddevice pyobjc-core \
  || fail "Couldn't install the app's Python libraries."
if ! run "Setting up its window" "$PY" -m pip install --user --quiet --disable-pip-version-check pywebview; then
  warn "It'll open in your web browser instead of its own window. Works just the same."
fi

# 6 — the command
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
