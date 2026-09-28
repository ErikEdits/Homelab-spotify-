#!/usr/bin/env bash
# Homify mit einem Befehl auf einen Ubuntu-Server holen und installieren:
#
#   curl -fsSL https://raw.githubusercontent.com/ErikEdits/Homelab-spotify-/main/ubuntu/bootstrap.sh | bash
#
# Optional:  HOMIFY_DIR=/opt/homify  HOMIFY_BRANCH=main
set -euo pipefail

DIR="${HOMIFY_DIR:-$HOME/homify}"
BRANCH="${HOMIFY_BRANCH:-main}"
REPO="https://github.com/ErikEdits/Homelab-spotify-.git"

if ! command -v git >/dev/null; then
  sudo apt-get update && sudo apt-get install -y git
fi

if [[ -d "$DIR/.git" ]]; then
  echo "Aktualisiere $DIR …"
  git -C "$DIR" fetch origin "$BRANCH"
  git -C "$DIR" checkout "$BRANCH"
  git -C "$DIR" pull --ff-only origin "$BRANCH"
else
  echo "Lade Homify nach $DIR …"
  git clone --branch "$BRANCH" "$REPO" "$DIR"
fi

cd "$DIR"
# Eingaben sollen vom Terminal kommen, auch wenn dieses Skript per „| bash“ läuft
if [[ -t 1 && -r /dev/tty ]]; then
  exec ./ubuntu/install.sh "$@" < /dev/tty
else
  exec ./ubuntu/install.sh --all
fi
