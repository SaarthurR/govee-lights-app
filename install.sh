#!/bin/sh
# Install/update Lights. --uninstall keeps state.json; --purge removes it too.
# curl -fsSL https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.sh | sh
set -eu

main() {
  dir="$HOME/govee-lights"
  label=${GOVEE_LABEL:-com.govee-lights.app}
  agent="$HOME/Library/LaunchAgents/$label.plist"
  unit="$HOME/.config/systemd/user/govee-lights.service"
  platform=$(uname -s)
  service=foreground
  if [ "$platform" = Darwin ]; then service=launchd
  elif command -v systemctl >/dev/null 2>&1 && systemctl --user show-environment >/dev/null 2>&1; then service=systemd
  fi
  uninstall=0; purge=0
  for arg in "$@"; do
    case "$arg" in --uninstall) uninstall=1;; --purge) uninstall=1; purge=1;; *) echo "Unknown option: $arg"; exit 1;; esac
  done
  if [ "$service" = launchd ] && [ -f "$agent" ]; then
    launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  elif [ -f "$unit" ]; then
    if [ "$service" != systemd ]; then
      echo "Existing Lights service needs a systemd user session. Run this from your logged-in desktop."; exit 1
    fi
    systemctl --user disable --now govee-lights.service
  fi
  if [ "$uninstall" = 1 ]; then
    rm -f "$agent" "$unit"
    if [ "$service" = systemd ]; then systemctl --user daemon-reload; fi
    if [ -d "$dir" ]; then
      find "$dir" -mindepth 1 -maxdepth 1 ! -name state.json -exec rm -rf {} +
      if [ "$purge" = 1 ]; then rm -rf "$dir"; fi
    fi
    echo "Lights uninstalled. State kept unless --purge was used."
    return
  fi
  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Lights needs Python 3.10 or newer: https://www.python.org/downloads/"; exit 1
  fi
  py=$(python3 -c 'import sys; print(sys.executable)')
  first=${PORT:-$(cat "$dir/.port" 2>/dev/null || echo 8765)}; port=$first
  while "$py" -c 'import socket,sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1)' "$port" 2>/dev/null; do
    if [ "$port" -ge $((first + 5)) ]; then echo "Ports $first to $port are busy."; exit 1; fi
    port=$((port + 1))
  done
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT HUP INT TERM
  curl -fsSL "${GOVEE_TARBALL:-https://github.com/SaarthurR/govee-lights-app/archive/refs/heads/main.tar.gz}" -o "$tmp/app.tar.gz"
  mkdir "$tmp/app"
  tar -xzf "$tmp/app.tar.gz" -C "$tmp/app" --strip-components=1
  test -f "$tmp/app/server.py"
  mkdir -p "$dir"
  # Preserve saved state even if an archive accidentally contains it.
  rm -f "$tmp/app/state.json"
  cp -R "$tmp/app/." "$dir/"
  echo "$port" > "$dir/.port"
  "$py" - "$service" "$dir" "$port" "$label" "$agent" "$unit" <<'PY'
import json, os, pathlib, plistlib, sys
service, directory, port, label, agent, unit = sys.argv[1:]
env = {"PORT": port}
# Carry dry-test settings into the isolated service, never real light traffic.
env.update({k: os.environ[k] for k in ("GOVEE_DRY", "GOVEE_FAKE_LIGHTS", "GOVEE_STATE") if k in os.environ})
if service == "launchd":
    p = pathlib.Path(agent); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(plistlib.dumps(dict(Label=label, ProgramArguments=[sys.executable, "-u", directory + "/server.py"],
        WorkingDirectory=directory, EnvironmentVariables=env, RunAtLoad=True, KeepAlive=True,
        StandardOutPath=directory + "/lights.log", StandardErrorPath=directory + "/lights.log")))
elif service == "systemd":
    p = pathlib.Path(unit); p.parent.mkdir(parents=True, exist_ok=True)
    def quote(s):
        return json.dumps(s).replace("%", "%%")
    p.write_text('[Unit]\nDescription=Lights\n[Service]\nType=simple\n' +
        'WorkingDirectory=' + quote(directory) + '\nExecStart=' + quote(sys.executable) + ' -u ' + quote(directory + '/server.py') + '\n' +
        ''.join('Environment=' + quote(k + '=' + v) + '\n' for k, v in env.items()) +
        'Restart=always\nRestartSec=3\n[Install]\nWantedBy=default.target\n')
PY
  case "$service" in
    launchd) launchctl bootstrap "gui/$(id -u)" "$agent";;
    systemd) systemctl --user daemon-reload; systemctl --user enable --now govee-lights.service;;
    foreground) echo "No systemd user session available; running in the foreground.";;
  esac
  echo "Lights: http://localhost:$port (run this installer again to update)."
  (i=0; while [ "$i" -lt 60 ]; do
    if curl -fs --max-time 1 "http://localhost:$port/api/state" >/dev/null 2>&1; then
      if command -v open >/dev/null 2>&1; then open "http://localhost:$port"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "http://localhost:$port"; fi
      break
    fi
    i=$((i + 1)); sleep 1
  done) >/dev/null 2>&1 &
  if [ "$service" = foreground ]; then cd "$dir"; PORT=$port exec "$py" server.py; fi
}
main "$@" # A cut-off download runs nothing.
