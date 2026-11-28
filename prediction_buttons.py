"""Persistent schedule buttons routed through the existing prediction queue."""
import re
import time
import threading
_click_lock = threading.Lock()
from common import APIError


def decorate(answer, matches, day):
    requests={}; keyboard=[]
    for match in matches:
        if match.status not in ('scheduled','live','break','extra','penalties'):
            continue
        key='fp:'+str(len(requests))
        requests[key]=dict(kind='football_prediction',day=day.isoformat(),
            filter=f'{match.home} — {match.away}',match_id=match.id,explicit_day=True)
        keyboard.append([dict(text=f'🔮 {match.home} — {match.away}'[:120],callback_data=key)])
    if keyboard:
        answer.reply_markup=dict(inline_keyboard=keyboard)
        answer.prediction_buttons=requests
    return answer


def remember(store, chat, message, answer):
    key=f'prediction:buttons:{chat}'
    now=time.time()
    saved={k:v for k,v in store.get(key,{}).items() if now-v.get('created',0)<7*86400}
    saved[str(message)]=dict(created=now,requests=answer.prediction_buttons)
    saved=dict(sorted(saved.items(),key=lambda item:item[1]['created'])[-40:])
    store.set(key,saved)


def handle(bot, query):
    def ack(text):
        try:bot.telegram.call('answerCallbackQuery',callback_query_id=query['id'],text=text)
        except APIError:pass
    message=query.get('message') or {}
    chat=message.get('chat',{}).get('id');mid=message.get('message_id')
    user=query.get('from') or {};data=query.get('data','')
    if (not isinstance(data,str) or not re.fullmatch(r'fp:\d{1,2}',data)
            or not bot.allowed(chat) or not bot.bot_id
            or message.get('from',{}).get('id')!=bot.bot_id
            or not user.get('id') or user.get('is_bot')):
        ack('Эта кнопка недоступна');return
    saved=bot.store.get(f'prediction:buttons:{chat}',{}).get(str(mid),{})
    req=saved.get('requests',{}).get(data)
    if not req or time.time()-saved.get('created',0)>7*86400:
        ack('Обнови расписание командой /football');return
    # Limit repeat clicks for the same match across all participants.
    with _click_lock:
        key=f'prediction:button_last:{chat}'
        now=time.time()
        recent={k:v for k,v in bot.store.get(key,{}).items() if now-v<30}
        identity=req['day']+':'+req['match_id']
        if identity in recent:
            ack('Прогноз уже запрошен. Подожди немного');return
        accepted=bot.enqueue(chat,text='Прогноз '+req['filter'],reply_to=mid,
            utility=dict(req),user_id=user['id'],speaker=user.get('first_name',''))
        if accepted:
            recent[identity]=now
            bot.store.set(key,recent)
    ack('Готовлю прогноз — он появится в чате' if accepted else 'Очередь занята. Попробуй чуть позже')



def completed_goal_links(store, chat, matches):
    """Link only confirmed video edits in this chat, never pending goal cards."""
    import json
    import sqlite3
    if not re.fullmatch(r'-100[1-9][0-9]*',str(chat)):
        return {}
    wanted={m.id for m in matches if m.status=='finished'}
    if not wanted:return {}
    result={}
    try:
        with store.db() as db:
            rows=db.execute("SELECT match_id,message_id,payload FROM goal_video_jobs WHERE chat_id=? AND status='attached' ORDER BY message_id",(chat,)).fetchall()
    except sqlite3.OperationalError as error:
        if 'no such table' in str(error):return {}
        raise
    for match_id,mid,payload in rows:
        if match_id not in wanted or type(mid) is not int or mid<=0:continue
        try:
            score=json.loads(payload)['goal']['score']
            if not isinstance(score,list) or len(score)!=2 or any(type(n) is not int or n<0 for n in score):continue
            number=sum(score)
            if number<=0:continue
        except (ValueError,KeyError,TypeError):continue
        result.setdefault(match_id,{})[number]=f'https://t.me/c/{str(chat)[4:]}/{mid}'
    return {match_id:'🎬 Голы: '+' · '.join(f'<a href="{url}">{number}</a>' for number,url in sorted(links.items()))
            for match_id,links in result.items()}
