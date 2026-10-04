#!/usr/bin/env python3
"""Interactive setup: enter the Telegram token only on your VM."""
import getpass
import json
import os
import re
import time

from common import APIError, CONFIG, MODEL, STATE, Telegram, http_json
from retrieval import build_index


def main():
    if CONFIG.exists():
        raise SystemExit('Настройка уже существует. Для изменения сделайте резервную копию state/config.json и отредактируйте её локально.')
    STATE.mkdir(exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    token = getpass.getpass('Вставьте токен НОВОГО бота (ввод скрыт): ').strip()
    if not re.fullmatch(r'\d{6,12}:[A-Za-z0-9_-]{25,}', token):
        raise SystemExit('Токен имеет неожиданный формат. Проверьте, что скопирован весь токен BotFather.')
    telegram = Telegram(token)
    identity = telegram.call('getMe')
    print(f'Бот проверен: @{identity["username"]}')
    if telegram.call('getWebhookInfo').get('url'):
        raise SystemExit('У этого бота уже настроен webhook. Создайте отдельного бота для проекта.')
    models = http_json('http://127.0.0.1:11434/api/tags', None)
    if not any(row.get('name') == MODEL or row.get('model') == MODEL for row in models.get('models', [])):
        raise SystemExit(f'Модель не найдена: {MODEL}')
    print('\nОткройте этого бота в своём Telegram и отправьте ему /start в ЛИЧНЫХ сообщениях.')
    input('После отправки нажмите Enter здесь: ')
    deadline = time.time() + 120
    users = {}
    last_update = -1
    scan_offset = 0
    while time.time() < deadline and not users:
        updates = telegram.call('getUpdates', offset=scan_offset, timeout=10, limit=100, allowed_updates=['message'])
        for update in updates:
            last_update = max(last_update, update['update_id'])
            msg = update.get('message', {})
            if (msg.get('chat', {}).get('type') == 'private'
                    and msg.get('text', '').split('@')[0].strip() == '/start'
                    and time.time() - msg.get('date', 0) < 600
                    and not msg.get('from', {}).get('is_bot')):
                user = msg['from']
                users[user['id']] = user
        scan_offset = last_update + 1
        if not users:
            print('Ожидаю /start от вас в личных сообщениях...', flush=True)
    if not users:
        raise SystemExit('Сообщение /start не найдено. Отправьте его новому боту и повторите настройку.')
    choices = list(users.values())
    print('\nВыберите СВОЙ аккаунт, который будет управлять ботом:')
    for i, user in enumerate(choices, 1):
        print(f'{i}. {user.get("first_name", "")} {user.get("last_name", "")} '
              f'@{user.get("username", "—")} — ID {user["id"]}')
    value = input('Номер аккаунта [1]: ').strip() or '1'
    if not value.isdigit() or not 1 <= int(value) <= len(choices):
        raise SystemExit('Неверный номер аккаунта; настройка не сохранена.')
    config = {'token': token, 'owner_id': choices[int(value) - 1]['id'],
              'model': MODEL, 'ollama_url': 'http://127.0.0.1:11434',
              'initial_offset': last_update + 1}
    count = build_index()
    descriptor = os.open(CONFIG, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as file:
        json.dump(config, file, ensure_ascii=False, indent=2)
    print(f'\nГотово. Индексировано примеров: {count}. Токен сохранён только на этой VM.')
    print('Теперь выполните: bash install_service.sh')
    print('В нужной группе отправьте /bubus — подключение и случайная фраза; /bubus текст — ответ по теме')
    print('Автоматические реплики по умолчанию выключены. Для включения: /auto_on')


if __name__ == '__main__':
    try:
        main()
    except APIError as error:
        raise SystemExit(f'Не удалось завершить настройку: {error}') from None
    except KeyboardInterrupt:
        raise SystemExit('Настройка прервана.') from None
