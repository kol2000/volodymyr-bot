#!/usr/bin/env bash
set -euo pipefail
project_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
project_user="$(id -un)"
if [[ "$project_user" == root ]]; then
    echo 'Запустите этот файл под botadmin, без sudo перед bash.' >&2
    exit 1
fi
if [[ ! -f "$project_dir/state/config.json" ]]; then
    echo 'Сначала выполните python3 setup.py в каталоге проекта.' >&2
    exit 1
fi
if [[ "$project_dir" != /home/botadmin/volodymyr_bot || "$project_user" != botadmin ]]; then
    echo 'Ожидается каталог /home/botadmin/volodymyr_bot и пользователь botadmin.' >&2
    exit 1
fi
chmod 700 "$project_dir/state"
chmod 600 "$project_dir/state/config.json"
sudo tee /etc/systemd/system/volodymyr-bot.service >/dev/null <<'UNIT'
[Unit]
Description=Volodymyr Telegram parody bot with local Ollama
Wants=network-online.target ollama.service
After=network-online.target ollama.service
StartLimitIntervalSec=0

[Service]
Type=simple
User=botadmin
Group=botadmin
WorkingDirectory=/home/botadmin/volodymyr_bot
ExecStart=/usr/bin/python3 -u /home/botadmin/volodymyr_bot/bot.py
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=10
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
sudo systemctl enable --now volodymyr-bot
sudo systemctl status volodymyr-bot --no-pager
