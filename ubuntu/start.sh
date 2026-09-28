#!/usr/bin/env bash
# Startet Homify im Vordergrund. Mit Desktop wird zusätzlich das Programmfenster geöffnet.
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  echo "Homify ist noch nicht installiert – bitte zuerst ./ubuntu/install.sh ausführen."
  exit 1
fi
export PYTHONUTF8=1
if [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then
  exec .venv/bin/python -m homify run --open "$@"
else
  exec .venv/bin/python -m homify run "$@"
fi
