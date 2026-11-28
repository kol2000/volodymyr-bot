#!/bin/bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo 'Run with sudo'; exit 1; fi
# Pause only the obsolete UEFA HTML research chain. No database changes.
systemctl is-active --quiet bubus-forecast-cycle.timer
[ "$(systemctl show bubus-forecast-cycle.service -p Result --value)" = success ]
backup=/home/botadmin/volodymyr_bot/state/maintenance_backups/uefa-pause-$(date -u +%Y%m%dT%H%M%SZ)
install -d -m 0700 "$backup"
units=(bubus-football-results bubus-football-observe bubus-football-shadow bubus-football-acceptance)
for unit in "${units[@]}"; do
  systemctl cat "$unit.service" "$unit.timer" > "$backup/$unit.units.txt"
  systemctl is-enabled "$unit.timer" > "$backup/$unit.enabled.txt" || true
done
for unit in "${units[@]}"; do
  systemctl disable --now "$unit.timer"
  systemctl stop "$unit.service"
  systemctl reset-failed "$unit.service" || true
done
printf '%s
' 'Paused UEFA-only research timers: upstream HTTP 403 and stale verified cache. Archives untouched. To resume after source repair: systemctl enable --now <saved timer names>.' > "$backup/REASON.txt"
systemctl start --no-block bubus-health.service
systemctl is-active bubus-forecast-cycle.timer
echo 'Four legacy timers paused; current forecast cycle retained; health check requested.'
