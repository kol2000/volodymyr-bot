import http.client
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from admin_panel import PanelServer, password_hash, local_ip
from panel_settings import SettingsDB, DEFAULTS, trigger_matches, validate, apply_football_request
from bot import TRIGGER_PATTERN, TRIGGER_FORMS, TRIGGER_WORDS, TRIGGER_PHRASES


class PanelTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'bot.sqlite3'
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)')
            db.executemany('INSERT INTO settings VALUES (?,?)',[
                ('chat_id','-100'),('token','"SECRET"'),('bank','991'),
                ('livefootball:enabled','true')])
            db.execute('CREATE TABLE goal_video_jobs(id INTEGER,status TEXT)')
            db.execute("INSERT INTO goal_video_jobs VALUES(1,'editing')")
        self.adapter=SettingsDB(self.path)

    def tearDown(self):self.temp.cleanup()

    def test_read_and_save_do_not_touch_bank_jobs_or_expose_secrets(self):
        first=self.adapter.read()
        self.assertNotIn('SECRET',json.dumps(first))
        self.adapter.save({'trigger_words':['ГОЛ','гол','Бубус'],'automatic':True},first['revision'])
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='bank'").fetchone()[0],'991')
            self.assertEqual(db.execute('SELECT status FROM goal_video_jobs').fetchone()[0],'editing')
        self.assertEqual(self.adapter.read()['settings']['trigger_words'],['гол','бубус'])

    def test_validation_is_atomic_and_stale_write_rejected(self):
        first=self.adapter.read()
        for bad in [{'token':'oops'}, {'automatic':1}, {'trigger_words':['']},
                    {'activity_probability':float('nan')},{'activity_probability':False},
                    {'trigger_words':['x\ny']},{'rules':'x'*2801}]:
            with self.assertRaises(ValueError):self.adapter.save(dict(automatic=True,**bad) if 'automatic' not in bad else bad,first['revision'])
        self.assertFalse(self.adapter.read()['settings']['automatic'])
        self.adapter.save({'automatic':True},first['revision'])
        with self.assertRaises(ValueError):self.adapter.save({'automatic':False},first['revision'])

    def test_football_toggle_is_deferred_and_has_bound_chat(self):
        self.adapter.save({'livefootball:enabled':False},self.adapter.read()['revision'])
        data=self.adapter.read()
        self.assertTrue(data['settings']['livefootball:enabled'])
        self.assertFalse(data['status']['football_pending']['enabled'])
        self.assertEqual(data['status']['football_pending']['chat_id'],-100)

    def test_missing_database_is_not_created(self):
        path=Path(self.temp.name)/'missing.sqlite3'
        with self.assertRaises(sqlite3.OperationalError):SettingsDB(path).read()
        self.assertFalse(path.exists())

    def test_trigger_defaults_and_explicit_empty(self):
        self.assertEqual(set(DEFAULTS['trigger_words']),set(TRIGGER_WORDS+TRIGGER_PHRASES))
        self.assertEqual(trigger_matches('Бубусу крым наш',None,TRIGGER_PATTERN,TRIGGER_FORMS),['бубус','крым наш'])
        self.assertEqual(trigger_matches('Бубусу крым наш',[],TRIGGER_PATTERN,TRIGGER_FORMS),[])

    def test_custom_triggers_boundaries_aliases_and_regex_literal(self):
        self.assertEqual(trigger_matches('голова Бубусу гол! НУ   ДА a+b',['гол','бубус','ну да','a+b'],TRIGGER_PATTERN,TRIGGER_FORMS),['бубус','гол','ну да','a+b'])
        self.assertEqual(trigger_matches('бубусу',['гол'],TRIGGER_PATTERN,TRIGGER_FORMS),[])

    def test_worker_applies_request_once_and_rejects_expired_or_rebound(self):
        state={'chat_id':-100,'panel:football_request':dict(id='a',enabled=False,created=time.time(),chat_id=-100)}
        bot=SimpleNamespace(store=SimpleNamespace(get=lambda k,default=None:state.get(k,default),set=lambda k,v:state.update({k:v})),football=Mock(),goal_videos=Mock())
        apply_football_request(bot);apply_football_request(bot)
        bot.football.answer.assert_called_once_with(dict(kind='football_switch',enabled=False),-100)
        bot.goal_videos.cancel.assert_called_once()
        for request in [dict(id='b',enabled=True,created=time.time()-301,chat_id=-100),dict(id='c',enabled=True,created=time.time(),chat_id=-200)]:
            state['panel:football_request']=request
            apply_football_request(bot)
            self.assertFalse(state['panel:football_result']['ok'])
        self.assertEqual(bot.football.answer.call_count,1)


class HTTPTests(PanelTests):
    def setUp(self):
        super().setUp()
        salt='12'*16
        self.server=PanelServer(('127.0.0.1',0),self.path,dict(salt=salt,hash=password_hash('test-password-123',salt)))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.host='127.0.0.1:'+str(self.server.server_port)
        self.cookie='';self.csrf=''

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        super().tearDown()

    def call(self,path,body=None,headers=None):
        connection=http.client.HTTPConnection(self.host,timeout=5)
        h={'Origin':'http://'+self.host,'Content-Type':'application/json','Cookie':self.cookie,'X-CSRF-Token':self.csrf}
        h.update(headers or {})
        connection.request('POST' if body is not None else 'GET',path,None if body is None else json.dumps(body),h)
        response=connection.getresponse();raw=response.read()
        result=(response.status,dict(response.getheaders()),json.loads(raw) if 'application/json' in response.getheader('Content-Type','') else raw)
        connection.close();return result

    def login(self):
        code,headers,_=self.call('/api/login',{'password':'test-password-123'})
        self.assertEqual(code,200)
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.cookie=headers['Set-Cookie'].split(';')[0]
        code,_,data=self.call('/api/settings');self.assertEqual(code,200)
        self.csrf=data['csrf'];return data

    def test_auth_origin_csrf_and_logout(self):
        self.assertEqual(self.call('/api/settings')[0],401)
        self.assertEqual(self.call('/api/login',{'password':'wrong'})[0],401)
        data=self.login()
        body={'settings':{'automatic':True},'revision':data['revision']}
        self.assertEqual(self.call('/api/settings',body,{'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.call('/api/settings',body,{'X-CSRF-Token':'wrong'})[0],403)
        self.assertEqual(self.call('/api/settings',body)[0],200)
        self.assertTrue(self.adapter.read()['settings']['automatic'])
        self.assertEqual(self.call('/api/logout',{})[0],200)
        self.assertEqual(self.call('/api/settings')[0],401)

    def test_login_throttle_and_host_guard(self):
        for _ in range(5):self.assertEqual(self.call('/api/login',{'password':'wrong'})[0],401)
        self.assertEqual(self.call('/api/login',{'password':'test-password-123'})[0],429)
        self.assertEqual(self.call('/',headers={'Host':'evil.example'})[0],403)
        self.assertFalse(local_ip('8.8.8.8'));self.assertTrue(local_ip('192.168.1.5'))

    def test_static_assets_and_no_private_files(self):
        for path in ['/','/panel.js','/panel.css']:
            code,headers,content=self.call(path)
            self.assertEqual(code,200);self.assertTrue(content)
            self.assertEqual(headers['X-Frame-Options'],'DENY')
        self.login()
        self.assertEqual(self.call('/state/config.json')[0],404)
        self.assertEqual(self.call('/../admin_panel.py')[0],404)


if __name__=='__main__':unittest.main()
