"""Weekly forecast audit. Preview by default; --send publishes once per week."""
import argparse
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from live_football import MOSCOW
from forecast_service import JOURNAL, checksum, identity, read_observation, write_once

LABELS = {'european_league': 'Европейские чемпионаты', 'rpl': 'РПЛ',
          'russian_cup': 'Кубок России', 'national': 'Сборные',
          'champions_league': 'Лига чемпионов', 'europa_league': 'Лига Европы'}


def period(now):
    local = now.astimezone(MOSCOW)
    end = local.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=local.weekday())
    # Persistent timers can catch up before Monday's publication time.
    if local < end + timedelta(hours=10):
        end -= timedelta(days=7)
    return end - timedelta(days=7), end


def outcome(score):
    return 0 if score[0] > score[1] else 2 if score[0] < score[1] else 1


def summarize(folder, start, end):
    groups = {}
    seen = set()
    for path in sorted((Path(folder) / 'observations').glob('*.json')):
        record = read_observation(path)
        if not start.timestamp() <= record['kickoff'] < end.timestamp():
            continue
        key = identity(record)
        if key in seen:
            continue
        seen.add(key)
        g = groups.setdefault(record['kind'], dict(total=0, settled=0, correct=0, exact=0))
        g['total'] += 1
        result_path = Path(folder) / 'results' / (key + '.json')
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text(encoding='utf-8'))
        score = result.get('score_90')
        if (result.get('forecast_sha256') != checksum(record)
                or result.get('match_id') != record['match_id']
                or result.get('competition') != record['competition']
                or not isinstance(score, list) or len(score) != 2
                or not all(type(v) is int and 0 <= v <= 30 for v in score)
                or result.get('actual') != outcome(score)):
            raise ValueError('Invalid weekly result: ' + key)
        g['settled'] += 1
        p = record['probabilities']
        pick = max(range(3), key=lambda i: p[i]) if p else outcome(record['score'])
        g['correct'] += pick == result['actual']
        g['exact'] += record['score'] == score
    return groups


def render(groups, start, end):
    totals = {k: sum(g[k] for g in groups.values()) for k in ('total', 'settled', 'correct', 'exact')}
    lines = ['⚽ Бубус подводит итоги прогнозов',
             f'{start:%d.%m.%Y} — {end - timedelta(days=1):%d.%m.%Y}', '',
             f"Прогнозов: {totals['total']}", f"Проверено результатов: {totals['settled']}"]
    n = totals['settled']
    if n:
        lines += [f"Угадал исход: {totals['correct']} из {n} ({totals['correct']/n:.0%})",
                  f"Точный счёт: {totals['exact']} из {n} ({totals['exact']/n:.0%})"]
    else:
        lines += ['Подтверждённых результатов пока нет — точность ещё не считаю.']
    lines += [f"Ждут подтверждения: {totals['total'] - n}", '']
    for kind, g in sorted(groups.items()):
        lines.append(f"{LABELS.get(kind, kind)}: исходы {g['correct']}/{g['settled']} · точный счёт {g['exact']}/{g['settled']} · прогнозов {g['total']}")
    lines += ['', 'Считаю матчи с началом на прошлой неделе по МСК. Только прогнозы до стартового свистка. Исход и счёт за 90 минут с добавленным временем.',
              'Это проверка экспериментальных прогнозов. Неподтверждённые результаты в точность не входят. Гарантий на следующую неделю нет.']
    return '\n'.join(lines)


def publish(folder, start, text, sender):
    directory = Path(folder) / 'weekly_reports' / start.date().isoformat()
    # An immutable claim survives even a timeout or a crash after Telegram accepted.
    if not write_once(directory / 'attempt.json', dict(text=text, created=datetime.now(MOSCOW).isoformat())):
        return 'already_attempted'
    try:
        response = sender(text)
        if not isinstance(response, dict) or not isinstance(response.get('message_id'), int):
            raise ValueError('Telegram acknowledgement missing')
        write_once(directory / 'sent.json', dict(message_id=response['message_id']))
    except Exception:
        write_once(directory / 'uncertain.json', dict(status='manual_review_required'))
        raise
    return 'sent'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--folder', type=Path, default=JOURNAL)
    args = parser.parse_args()
    start, end = period(datetime.now(MOSCOW))
    text = render(summarize(args.folder, start, end), start, end)
    if not args.send:
        print(text)
        return
    from common import Telegram, load_config, STATE
    with sqlite3.connect((STATE / 'bot.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        row = db.execute("SELECT value FROM settings WHERE key='chat_id'").fetchone()
    chat = json.loads(row[0]) if row else None
    if not isinstance(chat, int) or not chat:
        raise ValueError('No bound Telegram chat')
    telegram = Telegram(load_config()['token'])
    print(publish(args.folder, start, text, lambda value: telegram.send(chat, value)))


if __name__ == '__main__':
    main()
