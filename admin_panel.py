#!/usr/bin/env python3
"""Password-protected LAN panel; no Telegram client, model or writable Store."""
import argparse
import getpass
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from panel_settings import SettingsDB

ROOT=Path(__file__).resolve().parent


def local_ip(value):
    try:
        ip=ipaddress.ip_address(value)
        return ip.is_loopback or any(ip in n for n in
            (ipaddress.ip_network('10.0.0.0/8'),ipaddress.ip_network('172.16.0.0/12'),ipaddress.ip_network('192.168.0.0/16'))
            if ip.version==n.version)
    except ValueError:return False


def password_hash(password,salt):
    return hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()


class PanelServer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,address,db,credentials):
        self.db=SettingsDB(db);self.credentials=credentials
        self.sessions={};self.attempts={};self.guard=threading.Lock()
        super().__init__(address,Handler)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def log_message(self,*args):pass

    def send(self,status,data,mime='application/json; charset=utf-8',cookie=None):
        body=json.dumps(data,ensure_ascii=False).encode() if isinstance(data,(dict,list)) else data
        self.send_response(status)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                    'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY',
                    'Referrer-Policy':'no-referrer',
                    'Content-Security-Policy':"default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
            self.send_header(k,v)
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers();self.wfile.write(body)

    def access(self):
        try:host=urlsplit('//'+self.headers.get('Host','')).hostname
        except ValueError:host=''
        if not local_ip(self.client_address[0]) or not (host=='localhost' or local_ip(host or '')):
            self.send(403,dict(error='Доступ только из домашней сети'));return False
        return True

    def session(self):
        try:
            cookie=SimpleCookie(self.headers.get('Cookie',''));token=cookie['bubus_session'].value
        except (KeyError,ValueError,CookieError):return None,None
        with self.server.guard:
            value=self.server.sessions.get(token)
            if not value or value['expires']<time.time():
                self.server.sessions.pop(token,None);return None,None
            return token,value

    def do_GET(self):
        if not self.access():return
        path=urlsplit(self.path).path
        assets={'/':('index.html','text/html; charset=utf-8'),'/panel.css':('panel.css','text/css; charset=utf-8'),
                '/panel.js':('panel.js','text/javascript; charset=utf-8')}
        if path in assets:
            name,mime=assets[path];return self.send(200,(ROOT/'web_panel'/name).read_bytes(),mime)
        _,session=self.session()
        if not session:return self.send(401,dict(error='Войди в панель'))
        if path=='/api/settings':
            try:return self.send(200,dict(**self.server.db.read(),csrf=session['csrf']))
            except Exception:return self.send(503,dict(error='База Бубуса недоступна'))
        self.send(404,dict(error='Не найдено'))

    def do_POST(self):
        if not self.access():return
        origin=self.headers.get('Origin','')
        if origin!='http://'+self.headers.get('Host',''):
            return self.send(403,dict(error='Недопустимый источник запроса'))
        if self.headers.get('Content-Type','').split(';')[0]!='application/json':
            return self.send(415,dict(error='Ожидается JSON'))
        try:
            size=int(self.headers.get('Content-Length','0'))
            if not 0<size<=32768:raise ValueError()
            data=json.loads(self.rfile.read(size))
            if not isinstance(data,dict):raise ValueError()
        except (ValueError,UnicodeError):return self.send(400,dict(error='Некорректный запрос'))
        path=urlsplit(self.path).path
        if path=='/api/login':return self.login(data)
        token,session=self.session()
        if not session:return self.send(401,dict(error='Войди в панель'))
        if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),session['csrf']):
            return self.send(403,dict(error='Обнови страницу и повтори'))
        if path=='/api/logout':
            with self.server.guard:self.server.sessions.pop(token,None)
            return self.send(200,dict(ok=True),cookie='bubus_session=; Max-Age=0; Path=/; HttpOnly; SameSite=Strict')
        if path=='/api/settings':
            try:
                result=self.server.db.save(data.get('settings'),data.get('revision'))
                return self.send(200,dict(**result,csrf=session['csrf']))
            except ValueError as exc:return self.send(400,dict(error=str(exc)))
            except Exception:return self.send(503,dict(error='Не удалось сохранить настройки'))
        self.send(404,dict(error='Не найдено'))

    def login(self,data):
        password=data.get('password')
        if not isinstance(password,str) or len(password)>256:return self.send(400,dict(error='Некорректный пароль'))
        now=time.time();ip=self.client_address[0]
        with self.server.guard:
            self.server.attempts={k:[t for t in times if now-t<300] for k,times in self.server.attempts.items() if any(now-t<300 for t in times)}
            history=self.server.attempts.get(ip,[])
            if len(history)>=5 or sum(map(len,self.server.attempts.values()))>=50:
                return self.send(429,dict(error='Слишком много попыток. Подожди 5 минут.'))
            self.server.attempts[ip]=history+[now]
        credentials=self.server.credentials
        if not hmac.compare_digest(password_hash(password,credentials['salt']),credentials['hash']):
            return self.send(401,dict(error='Неверный пароль'))
        token=secrets.token_urlsafe(32)
        with self.server.guard:
            self.server.attempts.pop(ip,None)
            self.server.sessions={k:v for k,v in self.server.sessions.items() if v['expires']>now}
            if len(self.server.sessions)>=128:self.server.sessions.pop(next(iter(self.server.sessions)))
            self.server.sessions[token]=dict(csrf=secrets.token_urlsafe(32),expires=now+43200)
        self.send(200,dict(ok=True),cookie=f'bubus_session={token}; Max-Age=43200; Path=/; HttpOnly; SameSite=Strict')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--setup',action='store_true')
    parser.add_argument('--host',default='0.0.0.0');parser.add_argument('--port',type=int,default=8088)
    parser.add_argument('--db',type=Path,default=ROOT/'state/bot.sqlite3')
    parser.add_argument('--credentials',type=Path,default=ROOT/'state/panel-auth.json')
    args=parser.parse_args()
    if args.setup:
        password=getpass.getpass('Придумай пароль панели (минимум 12 символов): ')
        if len(password)<12 or len(password)>256:raise SystemExit('Нужно от 12 до 256 символов')
        if password!=getpass.getpass('Повтори пароль: '):raise SystemExit('Пароли не совпали')
        salt=secrets.token_hex(16)
        args.credentials.parent.mkdir(exist_ok=True)
        fd=os.open(args.credentials,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as stream:json.dump(dict(salt=salt,hash=password_hash(password,salt)),stream)
        args.credentials.chmod(0o600)
        print('Пароль панели сохранён.');return
    if not args.credentials.is_file():raise SystemExit('Сначала выполни python3 admin_panel.py --setup')
    credentials=json.loads(args.credentials.read_text())
    server=PanelServer((args.host,args.port),args.db,credentials)
    server.db.read() # refuse to start against a missing/incompatible database
    print(f'Панель Бубуса: порт {args.port}, доступ только из локальной сети',flush=True)
    server.serve_forever()


if __name__=='__main__':main()
