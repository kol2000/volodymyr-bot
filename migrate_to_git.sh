#!/usr/bin/env bash
set -euo pipefail
stage=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target=/home/botadmin/volodymyr_bot
backup="${target}.backup-$(date -u +%Y%m%d-%H%M%S)"
if [[ $(id -un) != botadmin || "$stage" == "$target" || "$stage" != /home/botadmin/* ]]; then
  echo 'Запустите от botadmin из отдельной копии репозитория в /home/botadmin/.' >&2
  exit 1
fi
if [[ ! -d "$stage/.git" || -e "$stage/state" || -e "$backup" ]]; then
  echo 'Нужен свежий checkout без state; каталог резервной копии должен быть свободен.' >&2
  exit 1
fi
if [[ ! -f "$target/state/config.json" || ! -f "$target/data/examples.jsonl" ]]; then
  echo 'В текущей установке не найден конфиг или корпус.' >&2
  exit 1
fi
mkdir -p "$stage/data"
cp -a -- "$target/data/examples.jsonl" "$stage/data/examples.jsonl"
cd -- "$stage"
python3 -m unittest discover -s tests -q
sudo -v
stopped=0
old_moved=0
new_installed=0
rollback() {
  result=$?
  if [[ $result -ne 0 && $stopped -eq 1 ]]; then
    echo 'Перенос не завершён; восстанавливаю прежнюю установку.' >&2
    sudo systemctl stop volodymyr-bot || true
    if [[ $new_installed -eq 1 ]]; then
      mv -- "$target" "$stage" || true
    fi
    if [[ $old_moved -eq 1 ]]; then
      mv -- "$backup" "$target" || true
    fi
    sudo systemctl start volodymyr-bot || true
  fi
  exit "$result"
}
trap rollback EXIT
sudo systemctl stop volodymyr-bot
stopped=1
cp -a -- "$target/state" "$stage/state"
mv -- "$target" "$backup"
old_moved=1
mv -- "$stage" "$target"
new_installed=1
cd -- "$target"
sudo systemctl start volodymyr-bot
sudo systemctl is-active volodymyr-bot
stopped=0
echo "Перенос завершён. Прежняя установка сохранена: $backup"
echo 'Дальнейшее обновление: bash /home/botadmin/volodymyr_bot/update.sh'
