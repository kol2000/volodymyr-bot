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
test_log=$(mktemp)
trap 'rm -f -- "$test_log"' EXIT
echo 'Проверка тестов…'
if python3 -m unittest discover -s tests -q >"$test_log" 2>&1; then
  echo 'Тесты пройдены.'
else
  test_status=$?
  echo 'Тесты завершились с ошибкой. Сервис не перезапущен. Подробности:' >&2
  cat "$test_log" >&2
  exit "$test_status"
fi
sudo systemctl restart volodymyr-bot
sudo systemctl is-active volodymyr-bot
echo 'Обновление завершено.'
