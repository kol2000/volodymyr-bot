"""Exercise deployment failures with a fake service manager, never systemd."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'service.log'
        self.env = dict(os.environ, PATH=str(self.bin) + ':' + os.environ['PATH'],
                        BOT_TEST_LOG=str(self.log), BOT_TEST_FAIL='')
        commands = {
            'id': 'echo botadmin',
            'git': '[ "$1" != pull ] || [ "$BOT_TEST_FAIL" != pull ]',
            'python3': '[ "$BOT_TEST_FAIL" != tests ]',
            'sudo': 'echo "$*" >> "$BOT_TEST_LOG"\n'
                    '[ "$*" != "systemctl is-active volodymyr-bot" ] || [ "$BOT_TEST_FAIL" != start ]',
        }
        for name, body in commands.items():
            p = self.bin / name
            p.write_text('#!/bin/sh\n' + body + '\n')
            p.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def run_script(self, script):
        return subprocess.run(['bash', str(script)], env=self.env,
                              capture_output=True, text=True, timeout=10)

    def test_update_failures_do_not_restart_service(self):
        for failure in ('pull', 'tests'):
            with self.subTest(failure=failure):
                self.env['BOT_TEST_FAIL'] = failure
                result = self.run_script(ROOT / 'update.sh')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.log.exists())

    def prepare_migration(self):
        home = self.root / 'botadmin'
        target = home / 'volodymyr_bot'
        stage = home / 'volodymyr_bot_checkout'
        for folder in (target / 'state', target / 'data', stage / '.git'):
            folder.mkdir(parents=True)
        (target / 'state/config.json').write_text('test-only-state')
        (target / 'data/examples.jsonl').write_text('test-only-corpus')
        script = stage / 'migrate_to_git.sh'
        script.write_text((ROOT / 'migrate_to_git.sh').read_text().replace('/home/botadmin', str(home)))
        return target, stage, script

    def test_migration_copies_state_and_retains_backup(self):
        target, stage, script = self.prepare_migration()
        result = self.run_script(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stage.exists())
        self.assertTrue((target / '.git').is_dir())
        self.assertEqual((target / 'state/config.json').read_text(), 'test-only-state')
        self.assertEqual((target / 'data/examples.jsonl').read_text(), 'test-only-corpus')
        backups = list(target.parent.glob('volodymyr_bot.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / 'state/config.json').read_text(), 'test-only-state')

    def test_failed_service_check_restores_original_installation(self):
        target, stage, script = self.prepare_migration()
        self.env['BOT_TEST_FAIL'] = 'start'
        result = self.run_script(script)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((target / '.git').exists())
        self.assertEqual((target / 'state/config.json').read_text(), 'test-only-state')
        self.assertTrue((stage / '.git').is_dir())
        self.assertEqual(list(target.parent.glob('volodymyr_bot.backup-*')), [])
        self.assertEqual(self.log.read_text().splitlines()[-1], 'systemctl start volodymyr-bot')
