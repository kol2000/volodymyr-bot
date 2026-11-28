#!/bin/bash
set -euo pipefail
cd /home/botadmin/volodymyr_bot
install -m 0644 bubus-forecast-cycle.service /etc/systemd/system/bubus-forecast-cycle.service
install -m 0644 bubus-forecast-cycle.timer /etc/systemd/system/bubus-forecast-cycle.timer
systemctl daemon-reload
systemctl enable --now bubus-forecast-cycle.timer
systemctl restart volodymyr-bot
systemctl is-active volodymyr-bot bubus-forecast-cycle.timer
