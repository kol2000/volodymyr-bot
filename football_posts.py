"""One persistent Telegram post: immediate score, growing goal details, video, VAR."""
import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, replace
from html import escape

from common import APIError, HTMLMessage, visible_text
from football_policy import excluded_competition
from goal_videos import caption, minute_values
from live_football import (ACTIVE, FINISHED, Goal, Match, PREFIX, already_started,
                           changes, event_text, goals_between, remembered_state)

LOG = logging.getLogger('volodymyr')


def snapshot(match):
    result = asdict(match)
    result.pop('goals', None)
    result.pop('cancellations', None)
    return result


def signature(payload):
    stable = {k: payload.get(k) for k in ('goals', 'cancelled', 'cancellation', 'corrected_score', 'score_pending')}
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def render(payload, limit=4096):
    match = Match(**payload['match'])
    goals = tuple(Goal(**dict(g, score=tuple(g['score']))) for g in payload['goals'])
    if not payload.get('cancelled'):
        text = event_text(match, payload['event'], payload['checked'], goals)
        if not goals:
            text = HTMLMessage(str(text).replace('\n<i>', '\nПодробности гола появятся после обновления трансляции.\n<i>', 1))
        return text
    current = payload['corrected_score']
    pending = payload.get('score_pending',False)
    corrected = (replace(match,home_score=None,away_score=None,label='Ожидаем исправления счёта источником') if pending
                 else replace(match, home_score=current[0], away_score=current[1]))
    title='⚽ Гол отменён · счёт уточняется' if pending else '⚽ Гол отменён · счёт исправлен'
    text = str(event_text(corrected, ('score', title), payload['checked']))
    header, footer = text.split('\n<i>', 1)
    footer = '\n<i>' + footer
    lines = []
    if goals:
        for goal in goals:
            label = {'own-goal': 'Автогол', 'penalty-goal': 'Гол с пенальти'}.get(goal.kind, 'Гол')
            line = f'{escape(goal.minute)} · {label} · {goal.score[0]}:{goal.score[1]}'
            removed = (pending and goal==goals[-1]) or any(a > c for a,c in zip(goal.score,current))
            lines.append(('<s>'+line+'</s>' if removed else '<b>'+line+'</b>') + '\n' + escape(goal.player))
    else:
        minute = match.label.split(' · ')[0] if re.match(r'^\d', match.label) else 'Гол'
        lines.append(f'<s>{escape(minute)} · Гол · {match.home_score}:{match.away_score}</s>')
    decision = payload.get('cancellation')
    badge = '<b>📺 VAR · гол отменён</b>\n' if decision and decision.get('var') else ''
    reason = decision['description'] if decision else 'Счёт уменьшился. Пояснение причины ждём от трансляции.'
    # Shorten only the explanation, preserving valid strike markup and the link.
    units = lambda s: len(visible_text(HTMLMessage(s)).encode('utf-16-le'))//2
    omitted=0
    while len(lines)>1 and units(header+'\n'.join(lines)+badge+footer)>limit-120:
        lines.pop(0)
        omitted+=1
    note=f'Ранее голов в этой карточке: {omitted}.\n' if omitted else ''
    prefix = header + '\n' + note + '\n'.join(lines) + '\n' + badge
    while units(prefix+escape(reason)+footer) > limit and reason:
        reason = reason[:-2].rstrip('…') + '…' if len(reason)>2 else ''
    return HTMLMessage(prefix+escape(reason)+footer)


class FootballPosts:
    def __init__(self, store, videos, telegram, clock=time.time):
        self.store, self.videos, self.telegram, self.clock = store, videos, telegram, clock
        with store.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS football_goal_posts (
                id INTEGER PRIMARY KEY, chat_id INTEGER, match_id TEXT, message_id INTEGER,
                created REAL, payload TEXT, desired TEXT, applied TEXT, send_state TEXT, edit_state TEXT,
                retry_at REAL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS football_posts_match ON football_goal_posts(chat_id,match_id);''')
            # Text/caption edits are idempotent on one message ID, unlike sends.
            db.execute("UPDATE football_goal_posts SET edit_state='idle' WHERE edit_state='editing'")
            db.execute("UPDATE football_goal_posts SET send_state='uncertain' WHERE send_state='sending'")

    def valid(self, chat, allowed):
        return (chat == self.store.get('chat_id') and allowed(chat) and
                self.store.get(PREFIX+'enabled', False) and self.clock() >= self.store.get('blocked_until', 0))

    def rows(self, chat, match_id):
        with self.store.db() as db:
            return db.execute('SELECT * FROM football_goal_posts WHERE chat_id=? AND match_id=? AND created>?',
                              (chat, match_id, self.clock()-6*3600)).fetchall()

    def decision(self, payload, match, cancelled):
        # Bind an explicit source decision only when it identifies one cancelled
        # post by author, score or a nearby source minute; never invent a reason.
        candidates = []
        for event in match.cancellations:
            text = event['description'].casefold()
            def evidence(p):
                goals = p['goals']
                minute = next(iter(minute_values(goals[-1]['minute'] if goals else p['match']['label'])), None)
                when = next(iter(minute_values(event['minute'])), None)
                if minute is not None and when is not None and when < minute:
                    return 0
                author = any(re.search(r'\b'+re.escape(g['player'].casefold().split()[-1]),text)
                             for g in goals if g['player'].strip())
                score = bool(re.search(r'(?<!\d)'+re.escape(f'{p["match"]["home_score"]}:{p["match"]["away_score"]}')+r'(?!\d)',text))
                return 2 if author or score else int(minute is not None and when is not None and 0 <= when-minute <= 10)
            best=max((evidence(p) for p in cancelled),default=0)
            if best and evidence(payload)==best and sum(evidence(p)==best for p in cancelled)==1:
                candidates.append(event)
        # FNK's feed is newest first: a later, more detailed verdict replaces
        # the first short "гол отменён" line for this same uniquely matched goal.
        return candidates[0] if candidates else payload.get('cancellation')

    def observe(self, chat, previous, match, checked, send, allowed):
        if excluded_competition(match.competition):
            return
        if not self.valid(chat, allowed) or None in (match.home_score, match.away_score):
            return
        now, current = self.clock(), [match.home_score, match.away_score]
        video_context = dict(goals=[asdict(g) for g in match.goals], score=current)
        self.videos.refresh_match_context(chat, match)
        before = previous.get('score') if previous else None
        if previous and previous.get('status') == 'scheduled' and not already_started(previous):
            before = [0, 0]
        increased = bool(before and None not in before and all(a >= b for a,b in zip(current,before)) and
                         sum(current)>sum(before) and match.status in ACTIVE|{FINISHED})
        decreased = bool(before and None not in before and any(a < b for a,b in zip(current,before)))
        events = changes(previous, match, now)
        latest = self.store.get(PREFIX+f'seen:{chat}', {})
        state = dict(remembered_state(previous, match, checked), announced_score=current, pending_goals=[])
        if any('⏸ Перерыв' in title for _,title in events):
            state['break_announced'] = True
        latest[match.id] = state
        self.store.set(PREFIX+f'seen:{chat}', {k:v for k,v in latest.items() if v.get('kickoff',0)>=now-2*86400})
        rows = self.rows(chat, match.id)
        payloads = []
        for row in rows:
            p = json.loads(row[5])
            after = [p['match']['home_score'], p['match']['away_score']]
            if any(a > c for a,c in zip(after,current)) and not p.get('cancelled'):
                p['cancelled'] = True
                p['corrected_score'] = current
            if p.get('cancelled') and any(a > c for a,c in zip(after,current)):
                p['score_pending']=False
                p['corrected_score']=current
            elif decreased and p.get('cancelled'):
                p['corrected_score'] = current
            if not p.get('cancelled'):
                ready = goals_between(match, p['before'], after)
                if ready:
                    p['goals'] = [asdict(g) for g in ready]
            payloads.append((row,p))
        # Sometimes the editor posts the VAR verdict before updating the board.
        # Show that verdict immediately, but do not guess the corrected score.
        all_payloads=[p for _,p in payloads]
        for _,p in payloads:
            if not p.get('cancelled'):
                decision=self.decision(p,match,all_payloads)
                if decision:
                    p.update(cancelled=True,cancellation=decision,corrected_score=current,score_pending=True)
        cancelled = [p for _,p in payloads if p.get('cancelled')]
        for row,p in payloads:
            context_changed = p.get('video_context') != video_context
            p['video_context'] = video_context
            if p.get('cancelled'):
                p['cancellation'] = self.decision(p,match,cancelled)
                with self.store.db() as db:
                    db.execute("UPDATE goal_video_jobs SET status='cancelled' WHERE chat_id=? AND message_id=? AND status='pending'", (chat,row[3]))
            sig = signature(p)
            if sig != row[6]:
                p['checked'] = checked
                with self.store.db() as db:
                    db.execute("UPDATE football_goal_posts SET payload=?,desired=?,edit_state=CASE WHEN edit_state='uncertain' THEN 'idle' ELSE edit_state END WHERE id=?",
                               (json.dumps(p,ensure_ascii=False),sig,row[0]))
            elif context_changed:
                with self.store.db() as db:
                    db.execute('UPDATE football_goal_posts SET payload=? WHERE id=?',
                               (json.dumps(p,ensure_ascii=False),row[0]))
        if increased:
            ready = goals_between(match,before,current)
            event = events[0] if events else ('score','⚽ Счет изменился')
            p = dict(match=snapshot(match), before=before, event=event, checked=checked,
                     goals=[asdict(g) for g in ready], cancelled=False, video_context=video_context)
            sig = signature(p)
            with self.store.db() as db:
                number = db.execute('INSERT INTO football_goal_posts (chat_id,match_id,created,payload,desired,applied,send_state,edit_state) VALUES (?,?,?,?,?,?,?,?)',
                                    (chat,match.id,now,json.dumps(p,ensure_ascii=False),sig,'','sending','idle')).lastrowid
            message = render(p)
            try:
                sent = send(chat,message)
            except Exception:
                with self.store.db() as db:
                    db.execute("UPDATE football_goal_posts SET send_state='uncertain' WHERE id=?",(number,))
                raise
            good = (isinstance(sent,dict) and type(sent.get('message_id')) is int and
                    sent.get('chat',{}).get('id')==chat)
            with self.store.db() as db:
                db.execute('UPDATE football_goal_posts SET message_id=?,send_state=?,applied=? WHERE id=?',
                           (sent['message_id'] if good else None,'confirmed' if good else 'uncertain',sig,number))
            if good and ready:
                self.videos.enqueue(chat,match,ready,sent['message_id'],message,created=now)
        elif events:
            # A rollback updates the goal's own post. Period transitions still
            # receive their one existing lifecycle notification.
            if decreased and rows:
                events = changes(dict(previous,score=current),match,now)
            if events:
                send(chat,event_text(match,events[0],checked))

    def poll(self, allowed):
        with self.store.db() as db:
            rows = db.execute("SELECT * FROM football_goal_posts WHERE send_state='confirmed' AND edit_state='idle' AND desired!=applied AND created>? AND retry_at<=? ORDER BY id",
                              (self.clock()-6*3600,self.clock())).fetchall()
        for old in rows:
            chat, message_id = old[1], old[3]
            if not self.valid(chat,allowed):
                continue
            # Serializes description/caption changes with the video commit,
            # while the score watcher remains independent of slow uploads.
            with self.videos.lock:
                with self.store.db() as db:
                    row = db.execute('SELECT * FROM football_goal_posts WHERE id=?',(old[0],)).fetchone()
                    video = db.execute('SELECT status FROM goal_video_jobs WHERE chat_id=? AND message_id=?',(chat,message_id)).fetchone()
                if not row or row[9]!='idle' or row[6]==row[7] or not self.valid(chat,allowed):
                    continue
                if video and video[0] in ('editing','manual_editing'):
                    continue
                payload, sig = json.loads(row[5]), row[6]
                if excluded_competition(payload['match']['competition']):
                    continue
                text = render(payload)
                job = dict(home=payload['match']['home'],away=payload['match']['away'],
                           competition=payload['match']['competition'],match_url=payload['match']['url'],
                           original=str(text),goals=payload['goals'],goal=payload['goals'][-1] if payload['goals'] else {})
                uncertain_media=bool(video and video[0]=='uncertain')
                media = bool(video and video[0] in ('attached','uncertain'))
                # Retain the strikethrough/VAR markup in a cancelled media card.
                outgoing = (render(payload,1024) if payload.get('cancelled') else self.media_text(text,job)) if media else text
                with self.store.db() as db:
                    changed = db.execute("UPDATE football_goal_posts SET edit_state='editing' WHERE id=? AND edit_state='idle'",(row[0],)).rowcount
                if not changed:
                    continue
                try:
                    try:
                        result = (self.telegram.edit_caption if media else self.telegram.edit_text)(chat,message_id,outgoing)
                    except APIError as error:
                        if not (uncertain_media and error.code==400 and not error.not_modified):
                            raise
                        # A lost upload acknowledgement does not tell us whether
                        # this ID is still text. Both edits are safe on that ID;
                        # neither fallback uploads a video or sends a new post.
                        media=False
                        outgoing=text
                        result=self.telegram.edit_text(chat,message_id,outgoing)
                    if not isinstance(result,dict) or result.get('message_id')!=message_id or result.get('chat',{}).get('id')!=chat:
                        raise APIError('Редактирование поста не подтверждено')
                except Exception as error:
                    # A timeout may have hidden a successful first edit. Telegram
                    # explicitly reporting identical content confirms this retry.
                    if not (isinstance(error,APIError) and error.not_modified):
                        code = error.code if isinstance(error,APIError) else 0
                        rate = isinstance(error,APIError) and code==429
                        retry = rate or not code or code>=500
                        if rate:
                            self.store.set('blocked_until',self.clock()+max(30,error.retry_after))
                        with self.store.db() as db:
                            db.execute('UPDATE football_goal_posts SET edit_state=?,retry_at=? WHERE id=?',
                                       ('idle' if retry else 'uncertain',self.clock()+max(30,error.retry_after if rate else 0),row[0]))
                        LOG.warning('Обновление гола не подтверждено: пост=%s; HTTP=%s',message_id,code)
                        continue
                with self.store.db() as db:
                    db.execute("UPDATE football_goal_posts SET applied=?,edit_state='idle' WHERE id=?",(sig,row[0]))
                    if uncertain_media and media:
                        db.execute("UPDATE goal_video_jobs SET status='attached' WHERE chat_id=? AND message_id=? AND status='uncertain'",(chat,message_id))
                    plain = visible_text(outgoing)
                    db.execute('UPDATE messages SET text=?,body=? WHERE chat_id=? AND message_id=? AND human=0',
                               (plain[:1000],plain[:3000],chat,message_id))
                if payload['goals'] and not payload.get('cancelled') and not media:
                    m = Match(**payload['match'])
                    goals = tuple(Goal(**dict(g,score=tuple(g['score']))) for g in payload['goals'])
                    context = payload.get('video_context', {})
                    full = tuple(Goal(**dict(g,score=tuple(g['score']))) for g in context.get('goals', payload['goals']))
                    score = context.get('score', [m.home_score, m.away_score])
                    m = replace(m, goals=full, home_score=score[0], away_score=score[1])
                    self.videos.enqueue(chat,m,goals,message_id,text,created=row[4])
                LOG.info('Пост гола обновлён: пост=%s; видео=%s; отменён=%s',message_id,media,bool(payload.get('cancelled')))

    @staticmethod
    def media_text(text, job):
        if len(visible_text(text).encode('utf-16-le'))//2 <=1024:
            return HTMLMessage(text)
        return caption(job)
