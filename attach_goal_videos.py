#!/usr/bin/env python3
"""Add videos to confirmed goal posts for one kickoff date; simplify existing video captions."""
import argparse
import hashlib
import json
import sqlite3
import time
from dataclasses import replace
from datetime import datetime

from common import APIError, CONFIG, STATE
from attach_goal_video import (AttachError, check_target, connected_telegram,
                               database, run as attach_one, setting)
from goal_videos import CHANNELS, VideoError, caption, matches_goal
from kulichki_football import fetch_html, parse_broadcast
from live_football import FootballError, Goal, MOSCOW, event_text
from replay_goal_videos import channel_history, jobs_for_posts, read_history


def prepare(day, path, pages, emit):
    selected, posts, statuses = read_history(path, day)
    if not selected:
        raise AttachError('В сохранённой выборке нет матчей за выбранную дату.')
    plans, failed = [], 0
    for row in selected:
        try:
            selection = {k: row[k] for k in ('id', 'url', 'home', 'away', 'competition')}
            selection['clock'] = datetime.fromtimestamp(row['kickoff'], MOSCOW).strftime('%H:%M')
            match = parse_broadcast(fetch_html(row['url']), selection)
            jobs = jobs_for_posts(match, posts)
            for job in jobs:
                job.update(competition=match.competition, match_url=match.url)
                # Re-read descriptions too: an early final snapshot may contain
                # only "ГОООЛ!". Keep original for check_target's concurrency guard.
                goals=tuple(Goal(**dict(g,score=tuple(g['score']))) for g in job['goals'])
                card=replace(match,home_score=goals[-1].score[0],away_score=goals[-1].score[1],
                             status='live',label=goals[-1].minute+' · матч идёт')
                job['render_original']=str(event_text(card,('score','⚽ Счет изменился'),job['created'],goals))
                plans.append((match, job, selection))
        except (FootballError, KeyError, ValueError) as error:
            failed += 1
            emit(f'Не удалось проверить {row["home"]} — {row["away"]}: {type(error).__name__}.')
    if not plans:
        raise AttachError('Подтверждённых постов Бубуса с голами за эту дату не найдено.')
    # A post cannot be edited under two different match interpretations.
    counts = {}
    for _, job, _ in plans:
        counts[job['message_id']] = counts.get(job['message_id'], 0) + 1
    clips = []
    if any(job['message_id'] not in statuses for _, job, _ in plans):
        for channel in CHANNELS:
            try:
                clips.extend(channel_history(channel, day, pages))
            except VideoError as error:
                emit(f'@{channel}: {error}; проверяю доступные источники.')
    result = []
    for match, job, selection in sorted(plans, key=lambda p: (p[1]['created'], p[1]['message_id'])):
        number = job['message_id']
        if counts[number] != 1:
            emit(f'Пост №{number}: неоднозначный матч; пропуск.')
            failed += 1
            continue
        state = statuses.get(number)
        if state == 'attached':
            result.append(('caption', (match, job, selection, None, job['original'])))
            continue
        if state is not None:
            emit(f'Пост №{number}: уже есть задание ({state}); повторной загрузки нет.')
            failed += 1
            continue
        options = [c for c in clips if matches_goal(c, job, time.time())
                   and sum(matches_goal(c, other, time.time()) for _, other, _ in plans) == 1]
        options.sort(key=lambda c: (CHANNELS.index(c.channel), -c.post_id))
        if not options:
            emit(f'Пост №{number}: подходящий отдельный видеопост не найден; пропуск.')
            failed += 1
            continue
        result.append(('video', (match, job, selection, options[0], job['original'])))
    return result, failed


def update_caption(path, prepared, telegram, emit=print):
    _, job, _, _, original = prepared
    number, text = job['message_id'], caption(job)
    if text == original:
        emit(f'Пост №{number}: видео уже добавлено, подпись лаконичная.')
        return 'skipped'
    digest = hashlib.sha256(text.encode()).hexdigest()
    with database(path) as db:
        chat = check_target(db, number, original, expected_status='attached')
        db.execute('CREATE TABLE IF NOT EXISTS goal_video_caption_jobs ('
                   'chat_id INTEGER,message_id INTEGER,digest TEXT,status TEXT,'
                   'PRIMARY KEY(chat_id,message_id))')
        previous = db.execute('SELECT digest,status FROM goal_video_caption_jobs WHERE chat_id=? AND message_id=?',
                              (chat, number)).fetchone()
        if previous:
            if previous == (digest, 'done'):
                emit(f'Пост №{number}: эта подпись уже применена.')
                return 'skipped'
            if previous[1] != 'done':
                raise AttachError(f'Пост №{number}: предыдущее изменение подписи {previous[1]}; автоматического повтора нет.')
        db.execute('INSERT OR REPLACE INTO goal_video_caption_jobs VALUES (?,?,?,?)',
                   (chat, number, digest, 'editing'))
    try:
        result = telegram.edit_caption(chat, number, text)
        if not (isinstance(result, dict) and result.get('message_id') == number and
                result.get('chat', {}).get('id') == chat and result.get('video', {}).get('file_id')):
            raise APIError('Ответ Telegram не подтвердил исходный пост с видео')
    except Exception as error:
        code = error.code if isinstance(error, APIError) else 0
        with database(path) as db:
            db.execute('UPDATE goal_video_caption_jobs SET status=? WHERE chat_id=? AND message_id=?',
                       ('uncertain' if not code or code >= 500 else 'failed', chat, number))
            if isinstance(error, APIError) and error.retry_after:
                db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',
                           ('blocked_until', json.dumps(time.time()+error.retry_after)))
        emit(f'Пост №{number}: изменение подписи не подтверждено, HTTP={code}; повтор не выполняется.')
        return 'failed'
    with database(path) as db:
        db.execute("UPDATE goal_video_caption_jobs SET status='done' WHERE chat_id=? AND message_id=?", (chat, number))
        db.execute('UPDATE messages SET text=?,body=? WHERE chat_id=? AND message_id=? AND human=0',
                   (text[:1000], text[:3000], chat, number))
    emit(f'Пост №{number}: подпись сокращена; видео осталось прежним.')
    return 'caption'


def run(day, path, config, pages=8, apply=False, emit=print):
    try:
        plans, failures = prepare(day, path, pages, emit)
        uploads = sum(kind == 'video' for kind, _ in plans)
        emit(f'Матчи {day:%d.%m.%Y} МСК: постов к проверке {len(plans)}; '
             f'добавление видео {uploads}; проверка подписей {len(plans)-uploads}.')
        if not apply:
            for kind, (_, job, _, clip, _) in plans:
                emit(f'Пост №{job["message_id"]}: ' + (clip.link if kind == 'video' else 'только подпись; видео не загружается'))
            emit('План без изменений. Для обработки существующих постов укажи --apply.')
            return int(bool(failures))
        if not plans:
            emit('Нет постов для обработки.')
            return int(bool(failures))
        telegram = connected_telegram(config, emit)
        added = changed = skipped = 0
        last_change = 0
        for kind, prepared in plans:
            match, job, _, clip, _ = prepared
            if kind == 'caption' and caption(job) == prepared[4]:
                skipped += 1
                emit(f'Пост №{job["message_id"]}: видео уже добавлено, подпись лаконичная.')
                continue
            # Leave room under group chat flood limits during a historical batch.
            delay = 3.1 - (time.monotonic() - last_change)
            if delay > 0:
                time.sleep(delay)
            try:
                if kind == 'caption':
                    outcome = update_caption(path, prepared, telegram, emit)
                    changed += outcome == 'caption'
                    skipped += outcome == 'skipped'
                    failures += outcome == 'failed'
                else:
                    result = attach_one(day, path, config, job['message_id'],
                                        f'{clip.channel}/{clip.post_id}', pages, True, emit,
                                        prepared=prepared, telegram=telegram)
                    added += result == 0
                    failures += result != 0
            except (AttachError, sqlite3.Error, ValueError, OSError) as error:
                failures += 1
                emit(str(error) if isinstance(error, AttachError) else
                     f'Пост №{job["message_id"]}: операция остановлена ({type(error).__name__}).')
            last_change = time.monotonic()
        emit(f'Итого: видео добавлено {added}; подписей сокращено {changed}; '
             f'уже готовы {skipped}; ошибок/пропусков {failures}. Новых сообщений не отправлено.')
        return int(bool(failures))
    except AttachError as error:
        emit(str(error))
    except APIError as error:
        emit(f'Telegram недоступен: HTTP={error.code}.')
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError) as error:
        emit('Обработка остановлена: ' + type(error).__name__ + '. Повторных отправок нет.')
    return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, type=lambda s: datetime.strptime(s, '%Y-%m-%d').date())
    parser.add_argument('--pages', type=int, choices=range(1, 9), default=8)
    parser.add_argument('--db', default=str(STATE/'bot.sqlite3'))
    parser.add_argument('--config', default=str(CONFIG))
    parser.add_argument('--apply', action='store_true', help='Edit the existing posts in the verified plan')
    args = parser.parse_args()
    return run(args.date, args.db, args.config, args.pages, args.apply)


if __name__ == '__main__':
    raise SystemExit(main())
