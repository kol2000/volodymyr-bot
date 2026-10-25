#!/usr/bin/env python3
"""Attach one verified goal video to one confirmed bot post; never send a new message."""
import argparse
import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from common import APIError, CONFIG, STATE, Telegram
from football_policy import excluded_competition
from goal_videos import CHANNELS, KEY, VideoError, caption, fetch_public, matches_goal
from kulichki_football import fetch_html, parse_broadcast
from live_football import FootballError, MOSCOW, PREFIX
from replay_goal_videos import channel_history, jobs_for_posts, read_history


class AttachError(Exception):
    pass


@contextmanager
def database(path):
    # mode=rw refuses to create a missing database; no Store/worker constructors.
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + '?mode=rw', uri=True, timeout=10)) as db:
        with db:
            db.execute('BEGIN IMMEDIATE')
            yield db


def setting(db, key, default=None):
    row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else default


def check_target(db, message_id, original, expected_chat=None, expected_status=None):
    chat = setting(db, 'chat_id')
    if type(chat) is not int or (expected_chat is not None and chat != expected_chat):
        raise AttachError('Подключённый чат изменился или не настроен.')
    if not setting(db, PREFIX + 'enabled', False) or not setting(db, KEY + 'enabled', True):
        raise AttachError('Футбол или добавление видео выключены в настройках бота.')
    if time.time() < setting(db, 'blocked_until', 0):
        raise AttachError('Действует ограничение Telegram; дождись окончания retry_after.')
    row = db.execute('SELECT COALESCE(body,text) FROM messages WHERE chat_id=? AND message_id=? AND human=0',
                     (chat, message_id)).fetchone()
    if not row or row[0] != original:
        raise AttachError('Исходный пост бота не найден или изменился после проверки.')
    previous = db.execute('SELECT status FROM goal_video_jobs WHERE chat_id=? AND message_id=?',
                          (chat, message_id)).fetchone()
    if expected_status is not None and (not previous or previous[0] != expected_status):
        raise AttachError('Состояние выбранного поста изменилось; операция отменена.')
    if previous and expected_status is None:
        raise AttachError('Для этого поста уже есть задание: ' + previous[0] + '. Повторное редактирование не выполняется.')
    return chat


def verified_plan(day, path, message_id, source_post, pages):
    if not re.fullmatch(r'(?:' + '|'.join(CHANNELS) + r')/[1-9]\d{0,11}', source_post):
        raise AttachError('Источник должен иметь вид idmanvideo/7210 или matchpremier/30000.')
    selected, posts, _ = read_history(path, day)
    target = [p for p in posts if p[0] == message_id]
    if len(target) != 1:
        raise AttachError('Подтверждённый пост Бубуса за эту дату не найден в истории подключённого чата.')
    plans = []
    for row in selected:
        # Do not fetch unrelated matches during a one-post test.
        from goal_videos import named, norm
        body = norm(target[0][2])
        if not (named(body, row['home']) and named(body, row['away'])):
            continue
        selection = {k: row[k] for k in ('id', 'url', 'home', 'away', 'competition')}
        selection['clock'] = datetime.fromtimestamp(row['kickoff'], MOSCOW).strftime('%H:%M')
        match = parse_broadcast(fetch_html(row['url']), selection)
        for job in jobs_for_posts(match, target):
            if (match.home_score is None or match.away_score is None or
                    any(s < g for s, g in zip((match.home_score, match.away_score), job['goal']['score']))):
                raise AttachError('Текущий счёт ФНК не подтверждает этот гол.')
            job.update(competition=match.competition, match_url=match.url)
            plans.append((match, job, selection))
    if len(plans) != 1:
        raise AttachError('Нельзя однозначно связать пост с подтверждённым голом ФНК.')
    match, job, selection = plans[0]
    channel, number = source_post.split('/')
    clips = [c for c in channel_history(channel, day, pages) if c.post_id == int(number)]
    if len(clips) != 1 or not matches_goal(clips[0], job, time.time()):
        raise AttachError('Указанный видеопост не найден или не совпадает с голом по командам, счёту, минуте и времени.')
    return match, job, selection, clips[0], target[0][2]


def check_cancelled(db, chat, message_id, match, job, pending=False):
    row = db.execute('SELECT match_id,expires,payload,status FROM goal_video_jobs WHERE chat_id=? AND message_id=?',
                     (chat, message_id)).fetchone()
    expected = 'pending' if pending else 'cancelled'
    if not row or row[3] != expected or (row[1] <= time.time() if pending else row[1] > time.time()):
        raise AttachError('Состояние или срок выбранного задания не допускает восстановление.')
    old = json.loads(row[2])
    target = job['goal']
    previous = old.get('goal', {})
    if (row[0] != match.id or excluded_competition(match.competition)
            or excluded_competition(old.get('competition'))
            or tuple(previous.get('score', ())) != tuple(target.get('score', ()))
            or any(previous.get(k) != target.get(k) for k in ('minute', 'player'))):
        raise AttachError('Сохранённое задание не подтверждает тот же разрешённый матч и гол.')


def reserve(path, chat, message_id, match, job, original, clip, digest, resume_cancelled=False, resume_pending=False):
    expected = 'pending' if resume_pending else 'cancelled' if resume_cancelled else None
    with database(path) as db:
        check_target(db, message_id, original, chat, expected)
        if expected:
            check_cancelled(db, chat, message_id, match, job, pending=resume_pending)
        if db.execute('SELECT 1 FROM goal_video_used WHERE chat_id=? AND (post=? OR digest=?)',
                      (chat, clip.link, digest)).fetchone():
            raise AttachError('Этот видеопост или файл уже использован в чате.')
        now = time.time()
        # manual_editing is not a pending job: the live worker cannot pick it up.
        # It remains reserved after a process crash and is never blindly retried.
        if expected:
            db.execute("UPDATE goal_video_jobs SET payload=?,status='manual_editing' WHERE chat_id=? AND message_id=? AND status=?",
                       (json.dumps(job, ensure_ascii=False), chat, message_id, expected))
        else:
            db.execute('INSERT INTO goal_video_jobs VALUES (?,?,?,?,?,?,?)',
                       (chat, message_id, match.id, now, now, json.dumps(job, ensure_ascii=False), 'manual_editing'))
        db.execute('INSERT INTO goal_video_used VALUES (?,?,?,?)', (chat, clip.link, digest, now))


def finish(path, chat, message_id, status, text=None):
    with database(path) as db:
        db.execute("UPDATE goal_video_jobs SET status=? WHERE chat_id=? AND message_id=? AND status='manual_editing'",
                   (status, chat, message_id))
        if status == 'attached':
            db.execute('UPDATE messages SET text=?,body=? WHERE chat_id=? AND message_id=? AND human=0',
                       (text[:1000], text[:3000], chat, message_id))


def connected_telegram(config_path, emit=print):
    try:
        config = json.loads(Path(config_path).read_text())
        token = config['token']
        if not isinstance(token, str) or not token:
            raise ValueError
    except (OSError, ValueError, KeyError, TypeError):
        raise AttachError('Не удалось прочитать токен из локальной конфигурации бота.') from None
    telegram = Telegram(token)
    me = telegram.call('getMe')
    if not isinstance(me, dict) or not me.get('is_bot'):
        raise AttachError('Telegram не подтвердил учётную запись бота.')
    emit('Telegram доступен: @' + str(me.get('username', 'бот')))
    return telegram


def run(day, path, config_path, message_id, source_post, pages=4, apply=False, emit=print,
        prepared=None, telegram=None, resume_cancelled=False, resume_pending=False):
    try:
        if resume_cancelled and resume_pending:
            raise AttachError('Выбери один режим восстановления.')
        expected = 'pending' if resume_pending else 'cancelled' if resume_cancelled else None
        match, job, selection, clip, original = prepared or verified_plan(day, path, message_id, source_post, pages)
        goal = job['goal']
        text = caption(job, clip)
        emit(f'Пост Бубуса №{message_id}: {match.home} — {match.away} · '
             f'{goal["score"][0]}:{goal["score"][1]} · {goal["minute"]} · {goal["player"]}')
        emit('Видео: ' + clip.link)
        emit('Будет изменён существующий пост. Новых сообщений и пересылок не будет.')
        if expected:
            with database(path) as db:
                chat = check_target(db, message_id, original, expected_status=expected)
                check_cancelled(db, chat, message_id, match, job, pending=resume_pending)
            emit('Проверено задание '+expected+'; восстанавливается только выбранный пост.')
        if not apply:
            emit('План проверен без изменений. Для добавления видео укажи --apply.')
            return 0
        # Preflight before downloading; repeat all local checks atomically after it.
        with database(path) as db:
            chat = check_target(db, message_id, original, expected_status=expected)
            if expected:
                check_cancelled(db, chat, message_id, match, job, pending=resume_pending)
        telegram = telegram or connected_telegram(config_path, emit)
        data = fetch_public(clip.url, video=True)
        emit(f'MP4 получен: {len(data)/1024/1024:.2f} МБ.')
        # Recheck FNK after the potentially slow download, before reserving/editing.
        fresh = parse_broadcast(fetch_html(match.url), selection)
        if (fresh.home_score is None or fresh.away_score is None or
                not any(g.score == tuple(goal['score']) and g.minute == goal['minute'] and
                        g.player == goal['player'] for g in fresh.goals) or
                any(s < g for s, g in zip((fresh.home_score, fresh.away_score), goal['score']))):
            raise AttachError('После скачивания ФНК больше не подтверждает этот гол. Редактирование отменено.')
        reserve(path, chat, message_id, match, job, original, clip, hashlib.sha256(data).hexdigest(), resume_cancelled, resume_pending)
        try:
            result = telegram.edit_video(chat, message_id, data, text)
            if not (isinstance(result, dict) and result.get('message_id') == message_id and
                    result.get('chat', {}).get('id') == chat and result.get('video', {}).get('file_id')):
                raise APIError('Ответ Telegram не подтвердил ожидаемый пост с видео')
        except Exception as error:
            code = error.code if isinstance(error, APIError) else 0
            finish(path, chat, message_id, 'uncertain' if not code or code >= 500 else 'failed')
            if isinstance(error, APIError) and error.retry_after:
                with database(path) as db:
                    db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                               ('blocked_until', json.dumps(time.time() + error.retry_after)))
            emit(f'Редактирование не подтверждено: HTTP={code}. Автоматического повтора нет; проверь исходный пост в чате.')
            return 1
        finish(path, chat, message_id, 'attached', text)
        emit(f'УСПЕХ: Telegram подтвердил видео в исходном посте №{message_id}. Новых сообщений не отправлено.')
        return 0
    except AttachError as error:
        emit(str(error))
    except (VideoError, FootballError) as error:
        emit('Источник недоступен: ' + str(error) + '. Редактирование не выполнялось.')
    except APIError as error:
        emit(f'Telegram недоступен до редактирования: HTTP={error.code}.')
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as error:
        # Fixed type only: arbitrary exception messages can contain credentials.
        emit('Проверка остановлена: ' + type(error).__name__ + '. Повторное редактирование не запускается.')
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=lambda s: datetime.strptime(s, '%Y-%m-%d').date())
    parser.add_argument('--message-id', required=True, type=int)
    parser.add_argument('--source-post', required=True, help='For example idmanvideo/7210')
    parser.add_argument('--pages', type=int, choices=range(1, 9), default=4)
    parser.add_argument('--db', default=str(STATE / 'bot.sqlite3'))
    parser.add_argument('--config', default=str(CONFIG))
    parser.add_argument('--apply', action='store_true', help='Actually edit the selected existing post once')
    recovery = parser.add_mutually_exclusive_group()
    recovery.add_argument('--resume-cancelled', action='store_true', help='Recover only this expired cancelled job after verification')
    recovery.add_argument('--resume-pending', action='store_true', help='Atomically claim only this verified pending job')
    args = parser.parse_args()
    return run(args.date, args.db, args.config, args.message_id, args.source_post, args.pages, args.apply,
               resume_cancelled=args.resume_cancelled, resume_pending=args.resume_pending)


if __name__ == '__main__':
    raise SystemExit(main())
