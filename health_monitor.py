"""Bubus diagnostics. Read-only by default; --write enables snapshots/backups/alerts."""
import argparse
from datetime import datetime
import hashlib,io,json,os,shutil,sqlite3,subprocess,tarfile,tempfile,time
from pathlib import Path

from common import ROOT,STATE,Telegram,load_config,http_json
from live_football import MOSCOW
from health_metrics import atomic

HEALTH=STATE/'health'
BACKUPS=STATE/'health_backups'
TIMERS=('bubus-forecast-cycle.timer','bubus-forecast-weekly.timer','bubus-chat-awards.timer','bubus-chat-memory.timer')


def result(status,detail):return dict(status=status,detail=detail,checked=time.time())


def command(*args):
    r=subprocess.run(args,capture_output=True,text=True,timeout=15)
    return r.stdout.strip()


def unit(name):return command('systemctl','is-active',name)


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default or {}


def digest(path):
    value=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):value.update(block)
    return value.hexdigest()


def db():return sqlite3.connect((STATE/'bot.sqlite3').resolve().as_uri()+'?mode=ro',uri=True,timeout=10)


def server():
    free=shutil.disk_usage(ROOT)
    mem={line.split(':')[0]:int(line.split()[1])*1024 for line in Path('/proc/meminfo').read_text().splitlines()}
    available=mem['MemAvailable']/mem['MemTotal'];load=os.getloadavg()[1]/(os.cpu_count() or 1)
    status='error' if free.free<1024**3 or available<.05 else 'warn' if free.free/free.total<.1 or available<.1 or load>1.5 else 'ok'
    return result(status,f'Диск свободно {free.free/1024**3:.1f} ГБ; RAM {available:.0%} свободно; load5/CPU {load:.2f}')


def sqlite_check():
    with db() as conn:
        conn.execute('PRAGMA busy_timeout=10000')
        values=conn.execute('PRAGMA quick_check').fetchall()
    return result('ok' if values==[('ok',)] else 'error','quick_check: OK' if values==[('ok',)] else 'Нарушена целостность SQLite; автоматическое восстановление запрещено')


def desktop():
    from desktop_reader import OUTPUT,REQUESTS
    beat=read(OUTPUT/'heartbeat.json');age=time.time()-beat.get('checked',0)
    states=[unit(n) for n in ('bubus-telegram-desktop','bubus-desktop-worker','bubus-desktop-export')]
    good=all(x=='active' for x in states) and 0<=age<600 and os.access(REQUESTS,os.W_OK)
    stalled=0
    for request in REQUESTS.glob('*.json'):
        if not 1800<time.time()-request.stat().st_mtime<7*86400:continue
        if not ((OUTPUT/(request.stem+'.json')).exists() and (OUTPUT/(request.stem+'.mp4')).exists()):stalled+=1
    return result('error' if not good else 'warn' if stalled else 'ok',f'Службы {"/".join(states)}; heartbeat {int(age)} с; запросов без результата >30 мин: {stalled}; очередь доступна {os.access(REQUESTS,os.W_OK)}')


def video_jobs():
    now=time.time()
    with db() as conn:
        counts=dict(conn.execute("SELECT status,count(*) FROM goal_video_jobs WHERE (status IN ('editing','uncertain','album_editing','album_uncertain','failed') AND created>?) OR (status='pending' AND expires<?) GROUP BY status",(now-7*86400,now-900)))
        stalled=conn.execute("SELECT count(*) FROM goal_video_jobs j LEFT JOIN goal_video_diagnostics d USING(chat_id,message_id) WHERE j.status='pending' AND j.created<? AND j.expires>? AND coalesce(d.checked,0)<?",(now-1200,now,now-1200)).fetchone()[0]
    return result('warn' if counts or stalled else 'ok',f'Просроченные/неопределённые: {counts}; без проверки 20 мин: {stalled}. Повтор отправки не выполняется')


def telegram_health(client):
    client.call('getMe')
    webhook=client.call('getWebhookInfo')
    recv=read(HEALTH/'telegram_receive.json');send=read(HEALTH/'telegram_send.json')
    if webhook.get('url'):return result('error','Установлен webhook при работающем long polling')
    if not recv:return result('unknown','API доступен; heartbeat polling ещё не получен')
    age=time.time()-recv.get('checked',0)
    bad=not recv.get('ok') or age>180
    send_bad=send and not send.get('ok') and time.time()-send.get('checked',0)<3600
    return result('error' if bad or send_bad else 'ok',f'API доступен; polling {int(age)} с назад; отправка '+('ошибка' if send_bad else 'последняя успешна' if send.get('ok') else 'ещё не наблюдалась'))


def models(config,vision=False):
    from inference import runner_options
    payload=dict(model=config.get('vision_model','gemma3:4b') if vision else config['model'],stream=False,
                 messages=[dict(role='user',content='Ответь только ОК')],options=runner_options(num_predict=24,temperature=0))
    endpoint=config['ollama_url']+'/api/chat'
    if vision:
        from PIL import Image
        from visual_context import jpeg
        stream=io.BytesIO();Image.new('RGB',(128,128),'red').save(stream,format='PNG')
        payload['messages']=[dict(role='user',content='Какой цвет на изображении? Ответь одним словом.',images=[jpeg(stream.getvalue())])]
        payload['options']=dict(num_ctx=4096,num_predict=24,temperature=0)
        endpoint=config.get('vision_url') or endpoint
    started=time.monotonic();response=http_json(endpoint,payload,timeout=75);seconds=time.monotonic()-started
    text=response.get('message',{}).get('content','').strip()
    valid=bool(text) and (not vision or 'красн' in text.lower() or 'red' in text.lower())
    return result('error' if not valid else 'warn' if seconds>60 else 'ok',f'Ответ за {seconds:.1f} с; '+('визуальная проба верна' if vision and valid else 'ответ получен' if valid else 'проба не пройдена'))


def sources_and_coverage():
    import kulichki_football as fnk
    import soccer365_football as soccer
    from forecast_service import JOURNAL,read_observation,competition_kind
    out={};reachable=0
    for name,fetch,parse,url in [('ФНК',fnk.fetch_html,fnk.parse_home,fnk.BASE+'/'),('Soccer365',soccer.fetch,lambda text:soccer.listing(text,include_regions=True),soccer.BASE+'/online/&date='+datetime.now(MOSCOW).date().isoformat())]:
        try:
            html=fetch(url);reachable+=1;rows=parse(html)
            out[name]=result('ok' if rows else 'unknown',f'Распознано матчей: {len(rows)}'+('' if rows else '; пустой календарь требует сверки'))
            if name=='Soccer365':
                try:
                    today=datetime.now(MOSCOW).date()
                    known=set()
                    for path in (JOURNAL/'observations').glob('*.json'):
                        r=read_observation(path)
                        if datetime.fromtimestamp(r['kickoff'],MOSCOW).date()==today:
                            known.add((r['kind'],soccer.identity(r['home']),soccer.identity(r['away'])))
                    expected={(competition_kind(r['competition']),soccer.identity(r['home']),soccer.identity(r['away'])) for r in rows if competition_kind(r['competition'])}
                    missing=expected-known
                    out['Полнота прогнозов']=result('warn' if missing else 'ok' if expected else 'unknown',f'Календарь сегодня: {len(expected)}; без записи прогноза: {len(missing)}. Исторические прогнозы не создаются')
                except Exception as error:
                    out['Полнота прогнозов']=result('error',type(error).__name__+' при проверке журнала прогнозов')
        except Exception as error:out[name]=result('error',type(error).__name__+' при получении/разборе данных')
    out['Сеть/VPN']=result('ok' if reachable==2 else 'warn','HTTP-доступ к источникам: '+str(reachable)+'/2. VPN на ВМ не управляется; удалённый туннель не перезапускается')
    return out


def forecasts():
    missing=[n for n in TIMERS if unit(n)!='active']
    last=command('systemctl','show','bubus-forecast-cycle.service','-p','ExecMainExitTimestamp','--value')
    timestamp=command('systemctl','show','bubus-forecast-cycle.service','-p','ExecMainExitTimestampMonotonic','--value')
    uptime=float(Path('/proc/uptime').read_text().split()[0])
    age=uptime-int(timestamp or 0)/1e6
    status=command('systemctl','show','bubus-forecast-cycle.service','-p','Result','--value')
    good=not missing and status=='success' and age<3*3600
    failed=command('systemctl','list-units','--state=failed','--no-legend','--plain').splitlines()
    legacy=[line.split()[0] for line in failed if line.startswith('bubus-') and 'forecast-cycle' not in line]
    return result('warn' if not good or legacy else 'ok',f'Неактивные таймеры: {len(missing)}; цикл {status}, {int(age/60)} мин назад; старые упавшие службы: {len(legacy)}')


def backup():
    BACKUPS.mkdir(mode=0o700,exist_ok=True)
    day=datetime.now(MOSCOW).date().isoformat();target=BACKUPS/(day+'.tar.gz')
    if target.exists():return
    if shutil.disk_usage(BACKUPS).free<1024**3:raise OSError('backup free space')
    with tempfile.TemporaryDirectory(dir=BACKUPS,prefix='.build-') as tmp:
        folder=Path(tmp);copy=folder/'bot.sqlite3'
        with db() as source:
            with sqlite3.connect(copy) as dest:
                source.backup(dest)
                if dest.execute('PRAGMA quick_check').fetchall()!=[('ok',)]:raise ValueError('backup integrity')
        archive=folder/'backup.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            tar.add(copy,arcname='state/bot.sqlite3')
            for rel in ('state/config.json','data/examples.jsonl','state/football_forecasts'):
                path=ROOT/rel
                if path.exists():tar.add(path,arcname=rel,filter=lambda info:None if info.issym() or info.islnk() else info)
        checksum=digest(archive)
        os.chmod(archive,0o600);os.replace(archive,target)
        atomic(target.with_suffix('.json'),dict(checked=time.time(),sha256=checksum,size=target.stat().st_size,sqlite='ok'))
    # Only our own dated files; do not touch pre-existing maintenance backups.
    for old in sorted(BACKUPS.glob('????-??-??.tar.gz'))[:-7]:
        if old.resolve().parent==BACKUPS.resolve():
            old.unlink();old.with_suffix('.json').unlink(missing_ok=True)


def backup_health():
    files=sorted(BACKUPS.glob('????-??-??.tar.gz'))
    if not files:return result('warn','Нет ежедневного проверенного локального бэкапа')
    path=files[-1];manifest=read(path.with_suffix('.json'));age=time.time()-manifest.get('checked',0)
    valid=age<30*3600 and manifest.get('size')==path.stat().st_size and manifest.get('sha256')==digest(path)
    return result('ok' if valid else 'error',f'Последний бэкап {int(age/3600)} ч назад; контрольная сумма '+('верна' if valid else 'не подтверждена')+'. Хранится на этой ВМ')


def incident_step(previous,checks,now):
    updated={};notices=[]
    for name,value in checks.items():
        old=previous.get(name,{})
        bad=value['status'] in ('warn','error')
        count=old.get('count',0)+(value['checked']>old.get('observed',0)) if bad else 0
        incident=dict(count=count,notified=old.get('notified',False),last=old.get('last',0),observed=value['checked'])
        if bad and count>=3 and (not incident['notified'] or now-incident['last']>=21600):
            notices.append('⚠ '+name+': '+value['detail'][:170]);incident.update(notified=True,last=now)
        elif not bad and incident['notified'] and value['status']=='ok':
            notices.append('✅ '+name+': восстановлено');incident.update(notified=False,last=now)
        updated[name]=incident
    return updated,notices


def run(write=False,deep=False):
    config=load_config();client=Telegram(config['token']);checks={}
    def check(name,fn):
        try:checks[name]=fn()
        except Exception as error:checks[name]=result('error',type(error).__name__+'; подробности скрыты')
    check('Бот',lambda:result('ok' if unit('volodymyr-bot')=='active' else 'error','Служба '+unit('volodymyr-bot')))
    check('Сервер',server);check('Telegram API',lambda:telegram_health(client));check('Telegram Desktop',desktop)
    check('Видео голов',video_jobs);check('Таймеры прогнозов',forecasts)
    checks.update(sources_and_coverage())
    old=read(HEALTH/'deep.json');age=time.time()-old.get('checked',0)
    due=deep or age>3600 or (age>240 and any(v['status']!='ok' for v in old.get('checks',{}).values()))
    if due:
        check('Ollama',lambda:models(config));check('Gemma',lambda:models(config,True));check('SQLite',sqlite_check)
        if write:atomic(HEALTH/'deep.json',dict(checked=time.time(),checks={k:checks[k] for k in ('Ollama','Gemma','SQLite')}))
    else:checks.update(old.get('checks',{}))
    if write:
        try:backup()
        except Exception as error:checks['Резервные копии']=result('error',type(error).__name__+' при создании бэкапа')
    if 'Резервные копии' not in checks:check('Резервные копии',backup_health)
    if write:
        atomic(HEALTH/'report.json',dict(checked=time.time(),components=checks))
        incidents,notices=incident_step(read(HEALTH/'incidents.json'),checks,time.time())
        # Record notification attempt BEFORE send; uncertain delivery is not retried immediately.
        atomic(HEALTH/'incidents.json',incidents)
        if notices:
            try:client.send(config['owner_id'],'🩺 Диагностика Бубуса\n\n'+'\n\n'.join(notices)[:3500])
            except Exception:atomic(HEALTH/'notification_failed.json',dict(checked=time.time(),status='delivery_uncertain_or_unavailable'))
    print(json.dumps(checks,ensure_ascii=False,indent=2))
    return checks


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--write',action='store_true');p.add_argument('--deep',action='store_true');a=p.parse_args();run(a.write,a.deep)
