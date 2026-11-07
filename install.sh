#!/bin/sh
# Lights: download the app into ~/govee-lights, start it and open it in your browser.
# Run it again to update; your state.json (lights, themes, timers) is kept.
#   curl -fsSL https://raw.githubusercontent.com/SaarthurR/govee-lights-app/main/install.sh | sh
set -eu

free() {  # nothing answers on this port (a bind test passes on macOS even while the app listens)
  ! python3 -c 'import socket, sys; socket.create_connection(("127.0.0.1", int(sys.argv[1])), 1)' "$1" 2>/dev/null
}

open_when_ready() {
  i=0
  while [ "$i" -lt 60 ]; do
    if curl -fs --max-time 1 "$1/api/state" >/dev/null 2>&1; then
      if command -v open >/dev/null 2>&1; then open "$1"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1; fi
      return 0
    fi
    i=$((i + 1)); sleep 1
  done
}

main() {
  url=${GOVEE_TARBALL:-https://github.com/SaarthurR/govee-lights-app/archive/refs/heads/main.tar.gz}
  dir="$HOME/govee-lights"

  if ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
    echo "Lights needs Python 3.10 or newer. Get it from https://www.python.org/downloads/ and run this again."
    exit 1
  fi

  first=${PORT:-8765}; port=$first
  until free "$port"; do
    if curl -fs --max-time 2 "http://localhost:$port/api/state" 2>/dev/null | grep -q '"phone"'; then
      echo "Lights is already running at http://localhost:$port. Stop it (Ctrl+C in its window), then run this again."
      exit 1
    fi
    if [ "$port" -ge $((first + 5)) ]; then
      echo "Ports $first to $port are all busy. Close something using one of them and run this again."
      exit 1
    fi
    port=$((port + 1))
  done

  echo "Downloading Lights into $dir"
  mkdir -p "$dir"
  tmp=$(mktemp)
  curl -fsSL "$url" -o "$tmp"
  tar -xzf "$tmp" -C "$dir" --strip-components=1  # state.json is not in the download, so yours stays
  rm -f "$tmp"

  open_when_ready "http://localhost:$port" &
  cd "$dir"
  PORT=$port exec python3 server.py
}

main  # last line, so a cut-off download runs nothing
