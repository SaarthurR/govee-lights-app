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
  uninstall=0; purge=0; update=0
  for arg in "$@"; do
    case "$arg" in --update-only) update=1;; --uninstall) uninstall=1;; --purge) uninstall=1; purge=1;; *) echo "Unknown option: $arg"; exit 1;; esac
  done
  stop_lights() {
    if [ "$update" = 0 ] && [ "$service" = launchd ] && [ -f "$agent" ]; then
      launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
    elif [ "$update" = 0 ] && [ -f "$unit" ]; then
      if [ "$service" != systemd ]; then
        echo "Existing Lights service needs a systemd user session. Run this from your logged-in desktop."; exit 1
      fi
      systemctl --user disable --now govee-lights.service
    fi
    if [ "$update" = 0 ] && [ -f "$dir/launcher/launch.py" ]; then
      "${GOVEE_PYTHON:-python3}" "$dir/launcher/launch.py" --stop
    fi
  }
  if [ "$uninstall" = 1 ]; then
    stop_lights
    rm -f "$agent" "$unit"
    rm -rf "$HOME/Applications/Govee Lights.app"
    rm -f "$HOME/.local/share/applications/govee-lights.desktop"
    if [ "$service" = systemd ]; then systemctl --user daemon-reload; fi
    if [ -d "$dir" ]; then
      find "$dir" -mindepth 1 -maxdepth 1 ! -name state.json -exec rm -rf {} +
      if [ "$purge" = 1 ]; then rm -rf "$dir"; fi
    fi
    echo "Lights uninstalled. State kept unless --purge was used."
    return
  fi
  py=${GOVEE_PYTHON:-python3}
  if ! "$py" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Lights needs Python 3.10 or newer: https://www.python.org/downloads/"; exit 1
  fi
  py=$("$py" -c 'import sys; print(sys.executable)')
  tmp=$(mktemp -d)
  stopped=0
  finish() {
    result=$?
    trap - EXIT HUP INT TERM
    set +e
    if [ "$result" != 0 ] && [ "$stopped" = 1 ] && [ -d "$tmp/old" ]; then
      echo "Install failed; restoring the previous Lights app." >&2
      restored=1
      stop_lights || restored=0
      find "$dir" -mindepth 1 -maxdepth 1 ! -name state.json ! -name state.json.bad ! -name .server.lock ! -name lights.log -exec rm -rf {} + || restored=0
      cp -R "$tmp/old/." "$dir/" || restored=0
      if [ -f "$tmp/agent" ]; then cp "$tmp/agent" "$agent" || restored=0; fi
      if [ -f "$tmp/unit" ]; then cp "$tmp/unit" "$unit" || restored=0; fi
      if [ -d "$tmp/mac-app" ]; then
        rm -rf "$HOME/Applications/Govee Lights.app" || restored=0
        cp -R "$tmp/mac-app" "$HOME/Applications/Govee Lights.app" || restored=0
      fi
      if [ -f "$tmp/desktop" ]; then cp "$tmp/desktop" "$HOME/.local/share/applications/govee-lights.desktop" || restored=0; fi
      if [ "$restored" = 1 ]; then
        case "$service" in
          launchd) launchctl bootstrap "gui/$(id -u)" "$agent" || restored=0;;
          systemd) systemctl --user daemon-reload && systemctl --user enable --now govee-lights.service || restored=0;;
          foreground) "$py" "$dir/launcher/launch.py" --url >/dev/null 2>&1 || restored=0;;
        esac
      fi
      if [ "$restored" = 0 ]; then
        echo "Recovery failed. Previous files are kept in $tmp" >&2
        exit "$result"
      fi
    fi
    rm -rf "$tmp"
    exit "$result"
  }
  trap finish EXIT
  trap 'exit 1' HUP INT TERM
  version=${GOVEE_VERSION:-}
  if [ -z "$version" ] && [ -z "${GOVEE_TARBALL:-}" ]; then
    # The GitHub API can rate-limit; then install main and let auto-update record the commit later.
    version=$(curl -fsSL https://api.github.com/repos/SaarthurR/govee-lights-app/commits/main 2>/dev/null | "$py" -c 'import json, sys; print(json.load(sys.stdin)["sha"])' 2>/dev/null) || version=
  fi
  curl -fsSL "${GOVEE_TARBALL:-https://github.com/SaarthurR/govee-lights-app/archive/${version:-main}.tar.gz}" -o "$tmp/app.tar.gz"
  mkdir "$tmp/app"
  tar -xzf "$tmp/app.tar.gz" -C "$tmp/app" --strip-components=1
  test -f "$tmp/app/server.py"
  "$py" -m py_compile "$tmp/app/server.py" "$tmp/app/launcher/launch.py" "$tmp/app/launcher/update.py" "$tmp/app/launcher/build.py"
  if [ "$update" = 0 ]; then
    if [ -d "$dir" ]; then
      cp -R "$dir" "$tmp/old"
      rm -f "$tmp/old/state.json" "$tmp/old/state.json.bad" "$tmp/old/.server.lock" "$tmp/old/lights.log"
    fi
    if [ -f "$agent" ]; then cp "$agent" "$tmp/agent"; fi
    if [ -f "$unit" ]; then cp "$unit" "$tmp/unit"; fi
    if [ -d "$HOME/Applications/Govee Lights.app" ]; then cp -R "$HOME/Applications/Govee Lights.app" "$tmp/mac-app"; fi
    if [ -f "$HOME/.local/share/applications/govee-lights.desktop" ]; then cp "$HOME/.local/share/applications/govee-lights.desktop" "$tmp/desktop"; fi
    stopped=1
    stop_lights
  fi
  first=${PORT:-$(cat "$dir/.port" 2>/dev/null || echo 8765)}; port=$first
  while [ "$update" = 0 ] && "$py" -c 'import socket,sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1)' "$port" 2>/dev/null; do
    if [ "$port" -ge $((first + 5)) ]; then echo "Ports $first to $port are busy."; exit 1; fi
    port=$((port + 1))
  done
  mkdir -p "$dir"
  # Preserve saved state even if an archive accidentally contains it.
  rm -f "$tmp/app/state.json"
  cp -R "$tmp/app/." "$dir/"
  echo "$port" > "$dir/.port"
  "$py" - "$service" "$dir" "$port" "$label" "$agent" "$unit" <<'PY'
import json, os, pathlib, plistlib, sys
service, directory, port, label, agent, unit = sys.argv[1:]
env = {"PORT": port, "PATH": os.environ.get("PATH", "/usr/bin:/bin")}  # so the service finds the claude command
# Carry dry-test settings into the isolated service, never real light traffic.
env.update({k: os.environ[k] for k in ("GOVEE_DRY", "GOVEE_FAKE_LIGHTS", "GOVEE_FAKE_SKUS", "GOVEE_STATE") if k in os.environ})
if service == "launchd":
    p = pathlib.Path(agent); p.parent.mkdir(parents=True, exist_ok=True)
    # Run as the app, not bare python: macOS only grants Local Network access to an app the user can allow.
    app = str(pathlib.Path.home() / "Applications/Govee Lights.app/Contents/MacOS/Govee Lights")
    p.write_bytes(plistlib.dumps(dict(Label=label, ProgramArguments=[app, "--server"],
        WorkingDirectory=directory, EnvironmentVariables=env, RunAtLoad=True, KeepAlive=True,
        StandardOutPath=directory + "/lights.log", StandardErrorPath=directory + "/lights.log")))
elif service == "systemd":
    p = pathlib.Path(unit); p.parent.mkdir(parents=True, exist_ok=True)
    def quote(s):
        return json.dumps(s).replace("%", "%%")
    p.write_text('[Unit]\nDescription=Lights\n[Service]\nType=simple\n' +
        'WorkingDirectory=' + quote(directory) + '\nExecStart=' + quote(sys.executable) + ' -u ' + quote(directory + '/launcher/launch.py') + ' --server' + '\n' +
        ''.join('Environment=' + quote(k + '=' + v) + '\n' for k, v in env.items()) +
        'Restart=always\nRestartSec=3\n[Install]\nWantedBy=default.target\n')
PY
  "$py" "$dir/launcher/build.py" "$service" "$label"
  echo "${version:-local}" > "$dir/.version"
  if [ "$update" = 1 ]; then return; fi
  case "$service" in
    launchd) launchctl bootstrap "gui/$(id -u)" "$agent";;
    systemd) systemctl --user daemon-reload; systemctl --user enable --now govee-lights.service;;
    foreground) echo "No systemd user session; the desktop launcher starts Lights on demand.";;
  esac
  echo "Lights: http://localhost:$port (run this installer again to update)."
  if [ "$platform" = Darwin ]; then
    open "$HOME/Applications/Govee Lights.app"
  else
    "$py" "$dir/launcher/launch.py" >/dev/null 2>&1 &
  fi
  stopped=0
}
main "$@"; exit # A cut-off download runs nothing; exit here, because an update overwrites this file while it runs.
