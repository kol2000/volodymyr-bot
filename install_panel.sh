#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
if [[ "$project_dir" != /home/botadmin/volodymyr_bot || "$(id -un)" != botadmin ]]; then
    echo 'Запусти под botadmin в /home/botadmin/volodymyr_bot, без sudo перед bash.' >&2
    exit 1
fi
cd "$project_dir"
if [[ ! -f state/bot.sqlite3 ]]; then
    echo 'Сначала настрой и запусти Бубуса: база ещё не создана.' >&2
    exit 1
fi
if [[ ! -f state/panel-auth.json ]]; then
    python3 admin_panel.py --setup
fi
chmod 600 state/panel-auth.json
sudo tee /etc/systemd/system/volodymyr-panel.service >/dev/null <<'UNIT'
[Unit]
Description=Bubus home network settings panel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=botadmin
Group=botadmin
WorkingDirectory=/home/botadmin/volodymyr_bot
ExecStart=/usr/bin/python3 -u /home/botadmin/volodymyr_bot/admin_panel.py --host 0.0.0.0 --port 8088
Restart=on-failure
RestartSec=5
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/home/botadmin/volodymyr_bot/state

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable volodymyr-panel
sudo systemctl restart volodymyr-bot volodymyr-panel
sudo systemctl is-active volodymyr-bot volodymyr-panel
echo 'Панель готова: http://192.168.1.5:8088'
