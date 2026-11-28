#!/bin/bash
set -euo pipefail
cd /home/botadmin/volodymyr_bot
runuser -u botadmin -- python3 -B chat_features.py install
for kind in awards memory; do
  install -m 0644 "bubus-chat-$kind.service" "/etc/systemd/system/bubus-chat-$kind.service"
  install -m 0644 "bubus-chat-$kind.timer" "/etc/systemd/system/bubus-chat-$kind.timer"
done
systemctl daemon-reload
systemctl enable --now bubus-chat-awards.timer bubus-chat-memory.timer
systemctl list-timers bubus-chat-awards.timer bubus-chat-memory.timer --no-pager
