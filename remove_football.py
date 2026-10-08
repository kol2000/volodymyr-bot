#!/usr/bin/env python3
"""Remove only retired API-Football settings; run while the bot is stopped."""
import json
import os
from pathlib import Path
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parent


def cleanup(root=ROOT):
    root = Path(root)
    state = root / 'state'
    config_path = state / 'config.json'
    config, key_removed = None, False
    if config_path.exists():
        config = json.loads(config_path.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('Invalid configuration')
        key_removed = 'football_api_key' in config
        config.pop('football_api_key', None)
    removed = 0
    db_path = state / 'bot.sqlite3'
    if db_path.exists():
        db = sqlite3.connect(db_path, timeout=30)
        try:
            with db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='settings'").fetchone():
                    removed = db.execute("DELETE FROM settings WHERE key GLOB 'football_*'").rowcount
        finally:
            db.close()
    if key_removed:
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=state,
                                             prefix='config-', suffix='.tmp', delete=False) as file:
                temporary = Path(file.name)
                os.chmod(temporary, 0o600)
                json.dump(config, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, config_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    compiled = 0
    for folder, module in ((root / '__pycache__', 'football'),
                           (root / '__pycache__', 'setup_football'),
                           (root / 'tests' / '__pycache__', 'test_football')):
        for path in folder.glob(module + '.*.pyc'):
            path.unlink()
            compiled += 1
    return key_removed, removed, compiled


if __name__ == '__main__':
    try:
        key_removed, settings, compiled = cleanup()
        print(f'API-Football: ключ {"удалён" if key_removed else "отсутствует"}; '
              f'настроек/кэшей удалено: {settings}; скомпилированных файлов: {compiled}.')
    except (OSError, ValueError, sqlite3.Error):
        raise SystemExit('Не удалось очистить локальные настройки API-Football. Проверьте права и корректность state/config.json; секреты не выводятся.') from None
