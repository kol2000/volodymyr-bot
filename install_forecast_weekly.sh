#!/bin/bash
set -euo pipefail
cd /home/botadmin/volodymyr_bot
install -m 0644 bubus-forecast-weekly.service /etc/systemd/system/bubus-forecast-weekly.service
install -m 0644 bubus-forecast-weekly.timer /etc/systemd/system/bubus-forecast-weekly.timer
systemctl daemon-reload
systemctl enable --now bubus-forecast-weekly.timer
systemctl list-timers bubus-forecast-weekly.timer --no-pager
