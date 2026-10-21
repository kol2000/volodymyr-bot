"""Read-only replay against confirmed bot posts. No Telegram client or writable Store."""
import hashlib
import json
import re
import sqlite3
import time
from contextlib import closing
from dataclasses import asdict
from datetime import datetime, time as daytime, timedelta
from html import unescape
from pathlib import Path

from goal_videos import (CHANNELS, TTL, VideoError, fetch_public, goal_match_reason,
                         matches_goal, minute_values, named, norm, parse_channel)
from kulichki_football import fetch_html, parse_broadcast, valid_url
from live_football import Document, FootballError, MOSCOW, PREFIX


def read_history(path, day):
    """Read only the bound chat's bot posts and retained FNK selection in one snapshot."""
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError('База бота не найдена; запускай команду на VM из каталога проекта.')
    start = datetime.combine(day, daytime(), MOSCOW).timestamp()
    end = datetime.combine(day + timedelta(days=1), daytime(6), MOSCOW).timestamp()
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=10)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        def setting(key, default=None):
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else default
        chat = setting('chat_id')
        selected = setting(PREFIX + 'fnk_selection', [])
        columns = {r[1] for r in db.execute('PRAGMA table_info(messages)')}
        body = 'COALESCE(body,text)' if 'body' in columns else 'text'
        posts = db.execute(f'SELECT message_id,timestamp,{body} FROM messages '
                           'WHERE chat_id=? AND human=0 AND timestamp>=? AND timestamp<? '
                           'ORDER BY timestamp,message_id', (chat, start, end)).fetchall()
        statuses = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='goal_video_jobs'").fetchone():
            statuses = dict(db.execute('SELECT message_id,status FROM goal_video_jobs WHERE chat_id=?', (chat,)))
    matches = [r for r in selected if start <= r.get('kickoff', 0) < start + 86400
               and valid_url(r.get('url', ''))]
    return matches, posts, statuses


def card_goals(match, body):
    """Only the goal-detail lines in an actual bot card, never its final-score header."""
    plain = unescape(re.sub(r'<[^>]+>', '', body))
    normalized = norm(plain)
    if not (named(normalized, match.home) and named(normalized, match.away)):
        return []
    found = []
    for goal in match.goals:
        score = rf'{goal.score[0]}\s*:\s*{goal.score[1]}'
        pattern = (r'(?<!\d)(\d{1,3}(?:\s*\+\s*\d{1,2})?\s*[′’\x27`])'
                   r'\s*·\s*(?:Автогол|Гол(?: с пенальти)?)\s*·\s*' + score + r'(?!\d)')
        expected = next(iter(minute_values(goal.minute)), None)
        recorded = [next(iter(minute_values(m[1])), None) for m in re.finditer(pattern, plain, re.I)]
        # An old bot card may retain the original minute after FNK corrects it.
        if expected is not None and any(v is not None and abs(v-expected) <= 1 for v in recorded):
            found.append(goal)
    return sorted(found, key=lambda g: sum(g.score))


def jobs_for_posts(match, posts):
    jobs = []
    for message_id, stamp, body in posts:
        goals = card_goals(match, body)
        if goals:
            jobs.append(dict(home=match.home, away=match.away, kickoff=match.kickoff,
                             created=stamp, message_id=message_id, original=body,
                             goal=asdict(goals[-1]), goals=[asdict(g) for g in goals]))
    return jobs


def channel_history(channel, day, pages, fetch=fetch_public, skipped=None):
    """Bounded public pagination; signed media URLs remain in memory only."""
    start = datetime.combine(day, daytime(), MOSCOW).timestamp()
    result, cursor = {}, None
    for _ in range(pages):
        url = 'https://t.me/s/' + channel + (f'?before={cursor}' if cursor else '')
        html = fetch(url)
        clips = parse_channel(html, channel, skipped=skipped)
        for clip in clips:
            result[clip.post_id] = clip
        ids = [int(m[1]) for n in Document(html).root.walk()
               if (m := re.fullmatch(re.escape(channel) + r'/(\d+)', n.attrs.get('data-post', '')))]
        if not ids or (cursor is not None and min(ids) >= cursor):
            break
        cursor = min(ids)
        if clips and min(c.timestamp for c in clips) < start:
            break
    return list(result.values())


def run_replay(day, path, pages=4, download=False, team='', emit=print):
    try:
        selected, posts, statuses = read_history(path, day)
    except (ValueError, sqlite3.Error, KeyError, TypeError) as error:
        emit(str(error) if isinstance(error, ValueError) else 'Не удалось прочитать историю бота: ' + type(error).__name__)
        return 1
    emit(f'Проверка матчей за {day:%d.%m.%Y} МСК · только чтение.')
    emit('База и очередь не изменяются; отправка и редактирование Telegram отключены.')
    selected = [s for s in selected if not team or norm(team) in norm(s['home'] + ' ' + s['away'])]
    if not selected:
        emit('За эту дату в сохранённом списке Бубуса нет матчей. Другая дата не подставляется.')
        return 1
    matches, jobs, failed = [], [], False
    for row in selected:
        try:
            selection = {k: row[k] for k in ('id', 'url', 'home', 'away', 'competition')}
            selection['clock'] = datetime.fromtimestamp(row['kickoff'], MOSCOW).strftime('%H:%M')
            match = parse_broadcast(fetch_html(row['url']), selection)
        except (FootballError, ValueError, KeyError, TypeError, AttributeError) as error:
            emit(f'{row["home"]} — {row["away"]}: ФНК недоступен ({type(error).__name__}: {error}).')
            failed = True
            continue
        matches.append(match)
        match_jobs = jobs_for_posts(match, posts)
        jobs.extend(match_jobs)
        emit(f'\n{match.home} — {match.away} · {match.home_score}:{match.away_score} · '
             f'голов с подробностями ФНК: {len(match.goals)} · найдено постов Бубуса: {len(match_jobs)}')
        if len(match.goals) != (match.home_score or 0) + (match.away_score or 0):
            emit('  В ФНК неполные подробности голов; полноту проверки подтвердить нельзя.')
        for goal in match.goals:
            covered = any(tuple(g['score']) == goal.score for j in match_jobs for g in j['goals'])
            if not covered:
                emit(f'  {goal.minute} · {goal.player} · {goal.score[0]}:{goal.score[1]}: '
                     'пост с подробностями не найден в локальной истории (не доказательство пропуска).')
    clips, skipped = [], []
    for channel in CHANNELS:
        try:
            found = channel_history(channel, day, pages, skipped=skipped)
            clips.extend(found)
            emit(f'\n@{channel}: доступных видеопостов в просмотренных страницах: {len(found)}')
        except VideoError as error:
            failed = True
            emit(f'\n@{channel}: недоступно ({error}).')
    now, matched, checked = time.time(), 0, {}
    for job in jobs:
        options = [c for c in clips if matches_goal(c, job, now)
                   and sum(matches_goal(c, j, now) for j in jobs) == 1]
        options.sort(key=lambda c: (CHANNELS.index(c.channel), -c.post_id))
        goal = job['goal']
        emit(f'\nПост №{job["message_id"]} · {job["home"]} — {job["away"]} · '
             f'{goal["score"][0]}:{goal["score"][1]} · {goal["minute"]} · {goal["player"]}')
        emit('  Состояние очереди: ' + statuses.get(job['message_id'], 'до включения видео / записи нет'))
        if not options:
            emit('  Подходящее видео в просмотренной части лент не найдено.')
            reasons = {'protected_post': 'защищённая публикация',
                       'ambiguous_caption': 'несколько подписей вне цитат; нельзя однозначно выбрать подпись видео',
                       'no_native_video': 'нет отдельного native-видео в превью',
                       'multiple_videos': 'несколько видео / альбом; выбор ролика неоднозначен',
                       'unsupported_media_url': 'превью не отдаёт разрешённую прямую MP4-ссылку'}
            seen = set()
            for candidate, reason in skipped:
                if candidate.link not in seen and matches_goal(candidate, job, now):
                    seen.add(candidate.link)
                    emit(f'  Подходящая подпись, ролик пропущен: {candidate.link} · {reasons[reason]}.')
            # Explain near matches without weakening the live matcher or
            # printing signed media links / unrelated public post contents.
            score = rf'(?<!\d){goal["score"][0]}\s*:\s*{goal["score"][1]}(?!\d)'
            for candidate in clips + [c for c, _ in skipped]:
                text = norm(candidate.text)
                if (candidate.link not in seen and named(text, job['home']) and named(text, job['away'])
                        and re.search(score, candidate.text)):
                    reason = goal_match_reason(candidate, job, now)
                    if reason:
                        seen.add(candidate.link)
                        minutes = ','.join(map(str, sorted(minute_values(candidate.text)))) or 'не указана'
                        emit(f'  Похожая подпись отклонена: {candidate.link} · причина={reason}; '
                             f'минута в канале={minutes}; видео относительно поста {candidate.timestamp-job["created"]:+.0f} сек.')
            continue
        matched += 1
        clip = options[0]
        difference = clip.timestamp - job['created']
        emit(f'  Совпадение: {clip.link}')
        emit(f'  Время публикации видео относительно поста Бубуса: {difference:+.0f} сек.')
        emit(f'  На момент прогона окно ожидания {TTL // 60} минут: ' +
             ('истекло; боевой обработчик старый пост не изменит.' if now >= job['created'] + TTL else 'ещё открыто.'))
        if clip.timestamp > job['created'] + TTL:
            emit('  Видео опубликовано после окна ожидания; обычная очередь его бы не дождалась.')
        if download:
            if clip.link not in checked:
                try:
                    data = fetch_public(clip.url, video=True)
                    checked[clip.link] = f'MP4 получен: {len(data)/1024/1024:.2f} МБ; SHA256 {hashlib.sha256(data).hexdigest()[:16]}'
                    del data
                except VideoError as error:
                    failed = True
                    checked[clip.link] = 'MP4 недоступен: ' + str(error)
            emit('  ' + checked[clip.link])
    emit(f'\nИтого: матчей проверено {len(matches)}; постов с голами {len(jobs)}; с подходящим видео {matched}.')
    emit('Это повторная проверка доступных сейчас публикаций, без ограничения давности старых постов. '
         'Она не доказывает доступность ролика в прошлом или успешное редактирование Telegram. '
         f'Просмотрено не более {pages} страниц каждого канала; отсутствие ролика не доказывает, что его не публиковали.')
    return int(failed or not jobs)
