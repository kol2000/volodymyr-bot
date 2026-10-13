#!/usr/bin/env python3
"""Check the public source on the VM, without Telegram, secrets or Ollama."""
import argparse
from datetime import datetime
from html import unescape
import re
import time
from live_football import FootballError, MOSCOW, scoreboard_text, normalize
from kulichki_football import BASE, KulichkiScoreboard, fetch_html, parse_home


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(MOSCOW).date().isoformat(), help='YYYY-MM-DD; default: today in Moscow')
    parser.add_argument('--team', default='', help='Team name, for example Ростов')
    parser.add_argument('--compare', action='store_true', help='Compare the daily page with direct match pages')
    parser.add_argument('--watch', type=int, default=0, help='Observe for 20–3600 seconds, refreshing every 20 seconds')
    args = parser.parse_args()
    try:
        day = datetime.strptime(args.date, '%Y-%m-%d').date()
    except ValueError:
        parser.error('Дата должна быть в формате YYYY-MM-DD.')
    if args.watch and not 20 <= args.watch <= 3600:
        parser.error('--watch: от 20 до 3600 секунд.')
    source = KulichkiScoreboard()
    if args.compare:
        try:
            started = time.monotonic()
            old = parse_home(fetch_html(BASE + '/?_=' + str(time.time_ns())))
            stamp = datetime.now(MOSCOW).strftime('%H:%M:%S МСК')
            print(f'Выборка главной ФНК · получено {stamp} · запрос {time.monotonic() - started:.2f} сек.', flush=True)
            for match in old:
                if not args.team or normalize(args.team) in normalize(match['home'] + ' ' + match['away']):
                    print(f'{match["clock"]} · {match["home"]} — {match["away"]} · {match["url"]}', flush=True)
        except FootballError as error:
            print(f'Главная ФНК недоступна: {error}', flush=True)
    deadline = time.monotonic() + args.watch
    while True:
        started = time.monotonic()
        try:
            matches, checked = source.day(day)
        except FootballError as error:
            print(f'Источник недоступен: {error}. Ничего в Telegram не отправлено.', flush=True)
            return 1
        print(unescape(re.sub(r'<[^>]+>', '', scoreboard_text(matches, day, checked, args.team))), flush=True)
        print(f'Получение и разбор: {time.monotonic() - started:.2f} сек.\n', flush=True)
        for match in matches:
            if args.team and normalize(args.team) not in normalize(match.home + ' ' + match.away):
                continue
            for goal in match.goals:
                print(f'Гол из трансляции: {goal.minute} · {goal.player} · {goal.score[0]}:{goal.score[1]} · {goal.kind}', flush=True)
        if not args.watch or time.monotonic() + 20 > deadline:
            break
        time.sleep(20)
    print('Это время получения данных. Источник не публикует точное время изменения счета; сравните с временем Telegram в журнале бота.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
