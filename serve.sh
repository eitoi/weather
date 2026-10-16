#!/bin/zsh
# Serve the app at http://localhost:8080 (and on your Wi-Fi so an iPhone can open it).
cd "$(dirname "$0")/site"
PORT="${PORT:-8080}"
IP=$(ipconfig getifaddr en0 2>/dev/null)
echo "Desktop:  http://localhost:$PORT"
[ -n "$IP" ] && echo "iPhone (same Wi-Fi):  http://$IP:$PORT"
exec ../.venv/bin/python -m http.server "$PORT" --bind 0.0.0.0
