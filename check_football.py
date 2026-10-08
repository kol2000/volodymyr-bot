#!/usr/bin/env python3
"""Check the public source on the VM, without Telegram, secrets or Ollama."""
import argparse
from datetime import datetime
from html import unescape
import re
from live_football import FootballError, MOSCOW, Scoreboard, scoreboard_text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=datetime.now(MOSCOW).date().isoformat(), help='YYYY-MM-DD; default: today in Moscow')
    parser.add_argument('--team', default='', help='Team name, for example Ростов')
    args = parser.parse_args()
    try:
        day = datetime.strptime(args.date, '%Y-%m-%d').date()
    except ValueError:
        parser.error('Дата должна быть в формате YYYY-MM-DD.')
    try:
        matches, checked = Scoreboard().day(day)
    except FootballError as error:
        print(f'Источник недоступен: {error}. Ничего в Telegram не отправлено.')
        return 1
    print(unescape(re.sub(r'<[^>]+>', '', scoreboard_text(matches, day, checked, args.team))))
    print('\nПроверка доступа завершена. Скорость live-обновлений проверяется во время игры.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
