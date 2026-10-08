#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if [[ $(id -un) != botadmin ]]; then
  echo 'Запускайте обновление от botadmin.' >&2
  exit 1
fi
git rev-parse --is-inside-work-tree >/dev/null
if ! git diff --quiet HEAD --; then
  echo 'Есть локальные изменения кода. Сохраните их перед обновлением.' >&2
  exit 1
fi
git pull --ff-only
python3 -m unittest discover -s tests -q
sudo systemctl stop volodymyr-bot
if ! python3 remove_football.py; then
  echo 'Очистка API-Football не завершена; запускаю бота обратно.' >&2
  sudo systemctl start volodymyr-bot
  exit 1
fi
sudo systemctl restart volodymyr-bot
sudo systemctl is-active volodymyr-bot
echo 'Обновление завершено.'
