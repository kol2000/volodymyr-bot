import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from bot import Store, COMMANDS
from remove_football import cleanup


class RemoveFootballTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'state').mkdir()
        self.path = self.root / 'state/config.json'

    def tearDown(self):
        self.temp.cleanup()

    def test_purge_preserves_other_secrets_history_salary_and_settings(self):
        config = {'token': 'test-telegram-token', 'football_api_key': 'test-retired-key',
                  'owner_id': 42, 'model': 'test-model', 'ollama_url': 'http://localhost:11434'}
        self.path.write_text(json.dumps(config))
        store = Store(self.root / 'state/bot.sqlite3')
        for key, value in {'football_quota': {'used': 3}, 'football_seen:-100': {'1': 'FT'},
                           'football_enabled': True, 'chat_id': -100, 'automatic': True,
                           'greetings_enabled': True, 'footballs': 'unrelated'}.items():
            store.set(key, value)
        with store.db() as db:
            db.execute('INSERT INTO salary_accounts VALUES (?,?,?,?)', (-100, 80, 1, 0))
            db.execute('INSERT INTO messages(chat_id,message_id,timestamp,speaker,text,human) VALUES (?,?,?,?,?,?)',
                       (-100, 1, 1000, 'Tester', 'saved history', 1))
        self.assertEqual(cleanup(self.root), (True, 3, 0))
        expected = dict(config)
        expected.pop('football_api_key')
        self.assertEqual(json.loads(self.path.read_text()), expected)
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(store.get('chat_id'), -100)
        self.assertTrue(store.get('automatic'))
        self.assertEqual(store.get('footballs'), 'unrelated')
        self.assertIsNone(store.get('football_quota'))
        with store.db() as db:
            self.assertEqual(db.execute('SELECT balance FROM salary_accounts').fetchone()[0], 80)
            self.assertEqual(db.execute('SELECT text FROM messages').fetchone()[0], 'saved history')
        self.assertEqual(cleanup(self.root), (False, 0, 0))

    def test_no_existing_installation_does_not_create_config_or_database(self):
        self.assertEqual(cleanup(self.root), (False, 0, 0))
        self.assertEqual(list((self.root / 'state').iterdir()), [])

    def test_config_without_key_is_not_rewritten(self):
        content = '{"token":"test-only"}\n'
        self.path.write_text(content)
        cleanup(self.root)
        self.assertEqual(self.path.read_text(), content)

    def test_failed_atomic_replace_keeps_original_configuration(self):
        content = '{"token":"test-only", "football_api_key":"test-retired-key"}'
        self.path.write_text(content)
        with patch('remove_football.os.replace', side_effect=OSError('test failure')):
            with self.assertRaises(OSError):
                cleanup(self.root)
        self.assertEqual(self.path.read_text(), content)
        self.assertEqual(list((self.root / 'state').glob('*.tmp')), [])

    def test_only_retired_compiled_modules_are_removed(self):
        folder = self.root / '__pycache__'
        folder.mkdir()
        for name in ('football.cpython-312.pyc', 'setup_football.cpython-312.pyc', 'bot.cpython-312.pyc'):
            (folder / name).write_bytes(b'test-only')
        self.assertEqual(cleanup(self.root), (False, 0, 2))
        self.assertEqual([p.name for p in folder.iterdir()], ['bot.cpython-312.pyc'])

    def test_retired_commands_not_advertised(self):
        self.assertFalse(any(cmd.startswith('/football') for cmd, _ in COMMANDS))
