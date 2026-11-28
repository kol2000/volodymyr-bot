import json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import health_monitor as h
from safe_recovery import allowed,UNITS


class HealthTests(unittest.TestCase):
    def test_incident_requires_fresh_checks_and_recovery_once(self):
        state={}
        for t in [1,1,2]:
            state,notices=h.incident_step(state,{'test':dict(status='error',detail='failed',checked=t)},t)
            self.assertEqual(notices,[])
        state,notices=h.incident_step(state,{'test':dict(status='error',detail='failed',checked=3)},3)
        self.assertEqual(len(notices),1)
        state,notices=h.incident_step(state,{'test':dict(status='error',detail='failed',checked=4)},4)
        self.assertEqual(notices,[])
        state,notices=h.incident_step(state,{'test':dict(status='ok',detail='ready',checked=5)},5)
        self.assertEqual(len(notices),1)
        state,notices=h.incident_step(state,{'test':dict(status='ok',detail='ready',checked=6)},6)
        self.assertEqual(notices,[])

    def test_recovery_rate_limit(self):
        self.assertTrue(allowed([],100000))
        self.assertFalse(allowed([99999],100000))
        self.assertFalse(allowed([80000,85000,90000],100000))
        self.assertTrue(allowed([1],100000))
        self.assertNotIn('bubus-telegram-desktop.service',UNITS)

    def test_backup_integrity_idempotence_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);state=root/'state';state.mkdir()
            with sqlite3.connect(state/'bot.sqlite3') as db:
                db.execute('CREATE TABLE sample(value TEXT)');db.execute("INSERT INTO sample VALUES ('test')")
            (state/'config.json').write_text('{}')
            with patch.object(h,'ROOT',root),patch.object(h,'STATE',state),patch.object(h,'BACKUPS',state/'backups'):
                h.backup();self.assertEqual(h.backup_health()['status'],'ok')
                first=list(h.BACKUPS.glob('*.tar.gz'));h.backup()
                self.assertEqual(first,list(h.BACKUPS.glob('*.tar.gz')))
                with first[0].open('ab') as file:file.write(b'bad')
                self.assertEqual(h.backup_health()['status'],'error')

    def test_telegram_no_long_poll_or_test_post(self):
        class Telegram:
            def __init__(self):self.calls=[]
            def call(self,name):self.calls.append(name);return {}
        client=Telegram()
        with patch.object(h,'read',return_value={}):
            self.assertEqual(h.telegram_health(client)['status'],'unknown')
        self.assertEqual(client.calls,['getMe','getWebhookInfo'])

    def test_sqlite_check_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            with sqlite3.connect(root/'bot.sqlite3') as db:db.execute('CREATE TABLE t(x)')
            with patch.object(h,'STATE',root):self.assertEqual(h.sqlite_check()['status'],'ok')

    def test_telemetry_failure_cannot_break_api(self):
        import health_metrics as metrics
        with patch.object(metrics.sys,'argv',['bot.py']),patch.object(metrics,'atomic',side_effect=OSError('disk')):
            metrics.telegram('getUpdates',True)

    def test_monitor_calls_cannot_mask_main_bot_heartbeat(self):
        import health_metrics as metrics
        with patch.object(metrics.sys,'argv',['health_monitor.py']),patch.object(metrics,'atomic') as write:
            metrics.telegram('sendMessage',True)
        write.assert_not_called()

    def test_coverage_excludes_previous_day_but_reports_today(self):
        from datetime import datetime
        from types import SimpleNamespace
        day=datetime(2026,11,28,tzinfo=h.MOSCOW).date()
        row=dict(competition='Чемпионат Англии',home='A',away='B')
        def match(d):return SimpleNamespace(kickoff=datetime(2026,11,d,23,tzinfo=h.MOSCOW).timestamp())
        old=h.coverage([row],set(),day,lambda r:match(27))
        self.assertNotEqual(old['status'],'warn');self.assertIn('другой даты исключены: 1',old['detail'])
        current=h.coverage([row],set(),day,lambda r:match(28))
        self.assertEqual(current['status'],'warn');self.assertIn('без записи прогноза: 1',current['detail'])

    def test_coverage_unknown_date_is_not_success(self):
        from datetime import datetime
        def fail(row):raise ValueError('offline')
        out=h.coverage([dict(competition='Чемпионат Англии',home='A',away='B')],set(),datetime.now(h.MOSCOW).date(),fail)
        self.assertEqual(out['status'],'warn');self.assertIn('дата не подтверждена: 1',out['detail'])


if __name__=='__main__':unittest.main()
