#!/usr/bin/env bash
# Homify – Installation für den Ubuntu-Homeserver
#
#   ./ubuntu/install.sh                  fragt, was eingerichtet werden soll
#   ./ubuntu/install.sh --all            alles: Dienst (Autostart) + Firewall + Tailscale
#   ./ubuntu/install.sh --service        nur zusätzlich als Dienst einrichten
#   ./ubuntu/install.sh --tailscale      nur zusätzlich Tailscale (Zugriff von unterwegs)
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
USER_NAME="$(id -un)"

WANT_SERVICE=""
WANT_TAILSCALE=""
WANT_FIREWALL=""
for arg in "$@"; do
  case "$arg" in
    --all) WANT_SERVICE=1; WANT_TAILSCALE=1; WANT_FIREWALL=1 ;;
    --service) WANT_SERVICE=1 ;;
    --tailscale) WANT_TAILSCALE=1 ;;
    --firewall) WANT_FIREWALL=1 ;;
    --yes|-y) WANT_SERVICE=${WANT_SERVICE:-1}; WANT_TAILSCALE=${WANT_TAILSCALE:-1}; WANT_FIREWALL=${WANT_FIREWALL:-1} ;;
    *) echo "Unbekannte Option: $arg"; exit 1 ;;
  esac
done

ask() {  # ask "Frage" -> 0 = ja
  local answer
  if [[ ! -t 0 ]]; then return 0; fi
  read -r -p "$1 [J/n] " answer
  [[ -z "$answer" || "$answer" =~ ^[JjYy] ]]
}

step() { echo; echo "==> $*"; }

echo
echo "  ┌─────────────────────────────────────────────┐"
echo "  │  Homify – dein eigenes Spotify fürs Homelab │"
echo "  │  Installation für den Ubuntu-Homeserver     │"
echo "  └─────────────────────────────────────────────┘"

if [[ $# -eq 0 ]]; then
  echo
  ask "Homify als Dienst einrichten (startet automatisch mit dem Server)?" && WANT_SERVICE=1
  ask "Firewall (ufw) für das Heimnetz öffnen?" && WANT_FIREWALL=1
  ask "Tailscale einrichten (Musik von unterwegs, sicher, ohne Router-Einstellungen)?" && WANT_TAILSCALE=1
fi

# ------------------------------------------------------------------ Python
step "Prüfe Python"
if ! command -v python3 >/dev/null; then
  sudo apt-get update && sudo apt-get install -y python3 python3-venv
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 oder neuer wird benötigt (gefunden: $(python3 --version))."
  exit 1
fi
if ! python3 -c 'import ensurepip, venv' 2>/dev/null; then
  sudo apt-get update && sudo apt-get install -y python3-venv
fi
python3 --version

step "Programmumgebung (.venv) und Pakete"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
.venv/bin/python -m pip install -r requirements.txt -q
echo "OK"

step "spotDL für Downloads (dauert ein paar Minuten)"
.venv/bin/python -m homify setup || echo "Hinweis: spotDL lässt sich später in Homify unter Einstellungen installieren."

PORT=$(.venv/bin/python -c 'from homify.config import config; print(config.get("port"))')

# ------------------------------------------------------------------ Dienst
if [[ -n "$WANT_SERVICE" ]]; then
  step "Richte systemd-Dienst ein"
  sed -e "s|@USER@|$USER_NAME|g" -e "s|@ROOT@|$ROOT|g" ubuntu/homify.service | sudo tee /etc/systemd/system/homify.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now homify
  sudo systemctl restart homify
  echo "Dienst läuft.  Status: systemctl status homify   Protokoll: journalctl -u homify -f"
fi

# ------------------------------------------------------------------ Firewall
if [[ -n "$WANT_FIREWALL" ]] && command -v ufw >/dev/null; then
  step "Firewall"
  if sudo ufw status | grep -q "Status: active"; then
    sudo ufw allow "$PORT/tcp" comment "Homify" >/dev/null
    echo "Port $PORT freigegeben."
  else
    echo "ufw ist nicht aktiv – nichts zu tun."
  fi
fi

# ------------------------------------------------------------------ Tailscale
if [[ -n "$WANT_TAILSCALE" ]]; then
  step "Tailscale (Zugriff von unterwegs)"
  if ! command -v tailscale >/dev/null; then
    curl -fsSL https://tailscale.com/install.sh | sh
  fi
  if ! tailscale status >/dev/null 2>&1; then
    echo "Gleich erscheint ein Link: im Browser öffnen und mit deinem Tailscale-Konto anmelden"
    echo "(dasselbe Konto später in der Tailscale-App auf dem Handy benutzen)."
    sudo tailscale up
  fi
  # Homify darf „tailscale serve“ selbst verwalten
  sudo tailscale set --operator="$USER_NAME" || true
  echo "Richte HTTPS-Adresse ein (tailscale serve) …"
  if sudo tailscale serve --bg "$PORT"; then
    TS_NAME=$(tailscale status --json 2>/dev/null | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' || true)
    [[ -n "$TS_NAME" ]] && echo "Unterwegs erreichbar unter:  https://$TS_NAME"
  else
    echo "HTTPS über Tailscale ging nicht. Meist müssen in der Tailscale-Verwaltung"
    echo "(https://login.tailscale.com/admin/dns) „MagicDNS“ und „HTTPS Certificates“ aktiviert werden."
    echo "Danach:  sudo tailscale serve --bg $PORT"
  fi
fi

# ------------------------------------------------------------------ Fertig
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
step "Fertig!"
echo "Im Heimnetz:  http://${IP:-<IP-des-Servers>}:${PORT}"
if [[ -z "$WANT_SERVICE" ]]; then
  echo "Starten mit:  ./ubuntu/start.sh      (oder als Dienst: ./ubuntu/install.sh --service)"
fi
echo
echo "Nächste Schritte:"
echo " 1. Adresse im Browser öffnen, Admin-Konto anlegen."
echo " 2. Einstellungen → Speicherort → NAS (UGREEN) eintragen und Musik übertragen."
echo " 3. Einstellungen → Apps: Android-App und Windows-App laden und diese Adresse eintragen."
