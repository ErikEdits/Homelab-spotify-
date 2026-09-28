#!/usr/bin/env bash
# Homify – Installation für Ubuntu/Debian
#   ./ubuntu/install.sh            installieren
#   ./ubuntu/install.sh --service  zusätzlich als systemd-Dienst einrichten (startet automatisch)
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
WITH_SERVICE=0
[[ "${1:-}" == "--service" ]] && WITH_SERVICE=1

echo
echo "  Homify – dein eigenes Spotify fürs Homelab"
echo "  Installation für Ubuntu"
echo

# 1. Python prüfen
if ! command -v python3 >/dev/null; then
  echo "python3 fehlt:  sudo apt install python3 python3-venv"
  exit 1
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
  echo "Python 3.10 oder neuer wird benötigt (gefunden: $(python3 --version))."
  exit 1
fi
if ! python3 -c 'import ensurepip, venv' 2>/dev/null; then
  echo "Das Paket python3-venv fehlt. Installiere es …"
  sudo apt-get update && sudo apt-get install -y python3-venv
fi

# 2. Programmumgebung
echo "[1/3] Erstelle Programmumgebung .venv …"
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip -q
echo "[2/3] Installiere Pakete …"
.venv/bin/python -m pip install -r requirements.txt

# 3. spotDL
echo "[3/3] Richte spotDL ein (dauert ein paar Minuten) …"
.venv/bin/python -m homify setup || echo "Hinweis: spotDL später in Homify unter Einstellungen installieren."

if [[ $WITH_SERVICE -eq 1 ]]; then
  echo "Richte systemd-Dienst ein …"
  sed -e "s|@USER@|$(id -un)|g" -e "s|@ROOT@|$ROOT|g" ubuntu/homify.service | sudo tee /etc/systemd/system/homify.service >/dev/null
  sudo systemctl daemon-reload
  sudo systemctl enable --now homify
  echo "Dienst läuft.  Status: systemctl status homify   Logs: journalctl -u homify -f"
else
  echo
  echo "Fertig! Starten mit:  ./ubuntu/start.sh"
  echo "Als Dienst (Autostart):  ./ubuntu/install.sh --service"
fi

PORT=$(.venv/bin/python -c 'from homify.config import config; print(config.get("port"))')
IP=$(hostname -I 2>/dev/null | awk '{print $1}')
echo
echo "Homify ist dann erreichbar unter:  http://localhost:${PORT}  bzw. im Netzwerk  http://${IP:-<IP>}:${PORT}"
if command -v ufw >/dev/null && sudo -n ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "Firewall (ufw) ist aktiv – Freigabe fürs Heimnetz:  sudo ufw allow ${PORT}/tcp"
fi
