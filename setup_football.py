#!/usr/bin/env python3
"""Add an API-Football key locally; preserve existing Telegram/model config."""
import getpass
import json
import os
import tempfile
from pathlib import Path

from bot import Store
from common import CONFIG, STATE, load_config
from football import Football, FootballError, error_reply


def save_key(key, path=CONFIG):
    # Reload at write time, preserving unrelated settings and credentials.
    config = json.loads(Path(path).read_text(encoding='utf-8'))
    config['football_api_key'] = key
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=Path(path).parent,
                                         prefix='config-', suffix='.tmp', delete=False) as file:
            temporary = Path(file.name)
            os.chmod(temporary, 0o600)
            json.dump(config, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def configure(key, config, store, path=CONFIG, transport=None):
    if not 8 <= len(key) <= 256 or any(char.isspace() for char in key):
        raise FootballError('key_invalid')
    football = Football(dict(config, football_api_key=key), store,
                        **({'transport': transport} if transport else {}))
    old_pause = store.get('football_pause', {})
    store.set('football_pause', {})
    try:
        football.leagues(force=True)
        # Test BOTH current seasons with actual fixtures, not just /leagues metadata.
        football.schedule(force=True)
        save_key(key, path)
    except BaseException:
        store.set('football_pause', old_pause)
        raise
    store.set('football_enabled', True)
    store.set(f'football_seen:{store.get("chat_id")}', None)
    store.set('football_next', 0)
    return football.remaining()


def main():
    if not CONFIG.exists():
        raise SystemExit('Сначала настрой Telegram-бота: python3 setup.py')
    config = load_config()
    print('Ключ из https://dashboard.api-football.com — Account → My Access.')
    print('Используется прямой API-Sports, не RapidAPI. Проверка расходует 3 запроса.')
    print('Проверю доступ к текущим сезонам РПЛ и Кубка России до сохранения ключа.')
    key = getpass.getpass('Вставь API-ключ (ввод скрыт): ').strip()
    remaining = configure(key, config, Store(STATE / 'bot.sqlite3'))
    print(f'Ключ сохранён локально. Уведомления включены; осталось запросов: {remaining}/100.')
    print('Теперь: sudo systemctl restart volodymyr-bot')
    print('В чате: /football. Отключить уведомления: /football_off (владельцу).')


if __name__ == '__main__':
    try:
        main()
    except FootballError as error:
        raise SystemExit(error_reply(str(error)) + '\nНовый ключ не сохранён; прежняя конфигурация сохранена.') from None
    except (OSError, ValueError):
        raise SystemExit('Не удалось прочитать или сохранить локальную конфигурацию.') from None
    except KeyboardInterrupt:
        raise SystemExit('Настройка прервана.') from None
