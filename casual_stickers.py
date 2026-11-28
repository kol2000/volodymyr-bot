"""Spaced sticker replacements for casual replies only; persistent attempt quota."""
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

SET_NAME='stickerset_n1_by_friedrich_bot'
KEY='casual_stickers'

def choose(store, job, thoughtful, utility, rng, now=None):
    if (thoughtful or utility or job.get('private') or job.get('random_quote') or job.get('tease_target')
            or job.get('response_control') or job.get('detailed') or not job.get('reply_to')):
        return None
    now=time.time() if now is None else now
    if job['chat_id']!=store.get('chat_id'): return None
    config=store.get(KEY,{})
    stickers=config.get('stickers',[]) if config.get('enabled') and config.get('set_name')==SET_NAME else []
    if not stickers: return None
    day=datetime.fromtimestamp(now,ZoneInfo('Europe/Moscow')).date().isoformat()
    key=f'{KEY}:quota:{job["chat_id"]}'
    with store.db() as db:
        db.execute('BEGIN IMMEDIATE')
        row=db.execute('SELECT value FROM settings WHERE key=?',(key,)).fetchone()
        quota=json.loads(row[0]) if row else {}
        if now-quota.get('last',0)<45*60: return None
        if quota.get('day')!=day:
            quota=dict(day=day,count=0,limit=rng.choice([10,11,12]),last=quota.get('last',0),file=quota.get('file',''))
        quota['limit']=max(10,min(12,quota.get('limit',10)))
        if quota['count']>=quota['limit']: return None
        if quota['count']>=10 and rng.random()>=0.35:return None
        choices=[s for s in stickers if s['file_id']!=quota.get('file')] or stickers
        sticker=rng.choice(choices)
        # Count the attempt before sending: an uncertain Telegram reply must not
        # cause repeats or bypass the daily cap after a restart.
        quota.update(count=quota['count']+1,last=now,file=sticker['file_id'])
        db.execute('INSERT OR REPLACE INTO settings(key,value) VALUES (?,?)',(key,json.dumps(quota)))
    return sticker

def send(telegram, chat_id, sticker, reply_to):
    with telegram.send_lock:
        delay=1.1-(time.monotonic()-telegram.last_send)
        if delay>0: time.sleep(delay)
        telegram.last_send=time.monotonic()
        return telegram.call('sendSticker',chat_id=chat_id,sticker=sticker['file_id'],
            reply_parameters=dict(message_id=reply_to,allow_sending_without_reply=True))
