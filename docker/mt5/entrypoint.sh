#!/bin/bash
# The desk's MT5 container (T143). Starts the virtual display (and VNC when VNC_PASSWORD is
# set), then keeps two processes alive, each restarted when it exits:
#   - the terminal in /mt5, logged in from MT5_ACCOUNT / MT5_PASSWORD / MT5_SERVER;
#   - the read-only bridge (Windows Python under Wine) on BRIDGE_PORT.
#
# Modes: `run` (default), or anything else executed as is (`bash`, for a look around).
set -euo pipefail

log() { echo "$(date -u +%H:%M:%S) entrypoint: $*"; }

: "${MT5_DIR:=/mt5}"
: "${BRIDGE_PORT:=18812}"
: "${SCREEN:=1280x800x24}"
RUN_DIR=/tmp/mt5
INI="$RUN_DIR/login.ini"
mkdir -p "$RUN_DIR"

winpath() { echo "Z:${1//\//\\}"; }

start_display() {
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
  Xvfb :99 -screen 0 "$SCREEN" -nolisten tcp -ac >/dev/null 2>&1 &
  for _ in $(seq 50); do [[ -S /tmp/.X11-unix/X99 ]] && break; sleep 0.2; done
  [[ -S /tmp/.X11-unix/X99 ]] || { log "Xvfb failed to start"; exit 1; }
  if [[ -n "${VNC_PASSWORD:-}" ]]; then
    x11vnc -storepasswd "$VNC_PASSWORD" "$RUN_DIR/vncpass" >/dev/null 2>&1
    x11vnc -display :99 -rfbport 5900 -rfbauth "$RUN_DIR/vncpass" \
      -forever -shared -noxdamage -quiet -bg -o "$RUN_DIR/x11vnc.log"
    log "VNC enabled on port 5900"
  fi
}

# The login comes from the environment, written to a private ini the terminal reads at start.
# Algo trading follows MT5_ALLOW_TRADING (T151), default 0. Off, the terminal is a data source
# and MT5 itself rejects every order the bridge's order path could send. On (=1), the `executor`
# worker can trade the configured account, whichever mode it is in. DLL imports stay off either
# way: the Python API needs none.
#
# MaxBars is set here, not in the GUI. The terminal starts with this file as its config, and a
# "Max bars in chart" change made over VNC did not survive a restart (2026-10-08). The API serves
# history only up to that limit, and the default of 100k is about three months of M1.
write_ini() {
  : "${MT5_ACCOUNT:?set MT5_ACCOUNT in .env}"
  : "${MT5_PASSWORD:?set MT5_PASSWORD in .env}"
  : "${MT5_SERVER:?set MT5_SERVER in .env}"
  : "${MT5_MAX_BARS:=2147483647}"
  local trade=0
  [ "${MT5_ALLOW_TRADING:-0}" = "1" ] && trade=1
  log "algo trading in the terminal: $([ $trade = 1 ] && echo ON || echo off)"
  umask 077
  printf '%s\r\n' \
    "[Common]" "Login=$MT5_ACCOUNT" "Password=$MT5_PASSWORD" "Server=$MT5_SERVER" \
    "KeepPrivate=1" "NewsEnable=0" \
    "[Charts]" "MaxBars=$MT5_MAX_BARS" \
    "[Experts]" "Enabled=$trade" "AllowLiveTrading=$trade" "AllowDllImport=0" >"$INI"
}

# The startup ini alone did not hold MaxBars (2026-10-09): it came back after a restart. So it is
# also written into the terminal's own settings, config/common.ini in the portable folder, before
# every start. That file is UTF-16LE with a BOM and CRLF, as the terminal writes it. The terminal
# rewrites it on exit, which is why this runs before each launch and not once.
set_max_bars() {
  local common="$MT5_DIR/config/common.ini"
  [[ -f "$common" ]] || { log "no $common yet, MaxBars only from the startup ini"; return 0; }
  python3 - "$common" "$MT5_MAX_BARS" <<'EOF' || log "could not set MaxBars in $common"
import sys
path, value = sys.argv[1], sys.argv[2]
raw = open(path, "rb").read()
text = raw.decode("utf-16")
lines = text.split("\r\n")
out, section, done = [], None, False
for line in lines:
    s = line.strip()
    if s.startswith("[") and s.endswith("]"):
        if section == "[Charts]" and not done:
            out.append(f"MaxBars={value}")
            done = True
        section = s
    elif section == "[Charts]" and s.lower().startswith("maxbars="):
        line, done = f"MaxBars={value}", True
    out.append(line)
if not done:
    if section != "[Charts]":
        out += ["[Charts]"]
    out.append(f"MaxBars={value}")
new = "\r\n".join(out)
if new != text:
    open(path, "wb").write(new.encode("utf-16"))  # "utf-16" writes the LE BOM
print(f"MaxBars={value} in {path}")
EOF
}

terminal_loop() {
  while :; do
    set_max_bars
    log "starting the terminal in $MT5_DIR (#$MT5_ACCOUNT on $MT5_SERVER)"
    wine "$MT5_DIR/terminal64.exe" /portable "/config:$(winpath "$INI")" >/dev/null 2>&1 || true
    log "terminal exited, restarting in 10s"
    sleep 10
  done
}

bridge_loop() {
  sleep 15  # the terminal needs a moment before initialize() can attach
  while :; do
    log "starting the bridge on :$BRIDGE_PORT"
    (cd /app && wine /opt/python/python.exe -u -m bridge.server \
      --terminal "$(winpath "$MT5_DIR/terminal64.exe")" --login "$MT5_ACCOUNT" \
      --port "$BRIDGE_PORT") || true
    log "bridge exited, restarting in 5s"
    sleep 5
  done
}

stop() {
  log "stopping"
  wine taskkill /im terminal64.exe >/dev/null 2>&1 || true
  sleep 5
  wineserver -k 2>/dev/null || true
  exit 0
}

mode="${1:-run}"
case "$mode" in
  run)
    [[ -f "$MT5_DIR/terminal64.exe" ]] || {
      log "no terminal64.exe in $MT5_DIR: copy the logged-in MT5 template there (see plans/charter-mt5/README.md)"
      exit 1
    }
    start_display
    write_ini
    trap stop TERM INT
    terminal_loop &
    bridge_loop &
    wait
    ;;
  *)
    exec "$@"
    ;;
esac
