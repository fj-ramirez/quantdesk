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
# AllowLiveTrading=0 and Experts disabled: this terminal is a data source (decision 2 of the
# plan). With an investor password MT5 refuses to trade regardless.
write_ini() {
  : "${MT5_ACCOUNT:?set MT5_ACCOUNT in .env}"
  : "${MT5_PASSWORD:?set MT5_PASSWORD in .env}"
  : "${MT5_SERVER:?set MT5_SERVER in .env}"
  umask 077
  printf '%s\r\n' \
    "[Common]" "Login=$MT5_ACCOUNT" "Password=$MT5_PASSWORD" "Server=$MT5_SERVER" \
    "KeepPrivate=1" "NewsEnable=0" \
    "[Experts]" "Enabled=0" "AllowLiveTrading=0" "AllowDllImport=0" >"$INI"
}

terminal_loop() {
  while :; do
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
