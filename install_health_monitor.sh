#!/bin/bash
set -euo pipefail
cd /home/botadmin/volodymyr_bot
install -o root -g root -m 0644 safe_recovery.py /usr/local/lib/bubus-safe-recovery.py
for unit in bubus-health.service bubus-health.timer bubus-safe-recovery.service bubus-safe-recovery.timer; do
  install -o root -g root -m 0644 "$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl restart volodymyr-bot
systemctl enable --now bubus-health.timer bubus-safe-recovery.timer
systemctl start --no-block bubus-health.service
systemctl list-timers bubus-health.timer bubus-safe-recovery.timer --no-pager
