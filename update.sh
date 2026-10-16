#!/bin/zsh
# Rebuild the forecast data. Add --loop to refresh every hour (keeps the Mac awake while running).
cd "$(dirname "$0")"
if [ "$1" = "--loop" ]; then
  shift
  while true; do
    caffeinate -is .venv/bin/python -m ingest.run "$@" || echo "update failed; retrying next hour"
    # HRRR runs land ~50-60 min after the hour; aim for :55
    now=$(date +%s); next=$(( (now / 3600 + 1) * 3600 + 55 * 60 - 3600 ))
    [ $next -le $now ] && next=$(( next + 3600 ))
    sleep $(( next - now ))
  done
else
  exec caffeinate -is .venv/bin/python -m ingest.run "$@"
fi
