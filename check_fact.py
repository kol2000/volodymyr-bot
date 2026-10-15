#!/usr/bin/env python3
"""Check public-source access on the VM; model generation is opt-in."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo

from common import load_config
from fact_check import FactChecker, FactError


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('query', help='public claim to look up')
    parser.add_argument('--answer', action='store_true', help='also ask the local model to evaluate the evidence')
    args = parser.parse_args()
    checker = FactChecker()
    try:
        sources, when = checker.evidence(args.query)
        print('Получено', datetime.fromtimestamp(when, ZoneInfo('Europe/Moscow')).strftime('%d.%m.%Y %H:%M МСК'))
        for row in sources:
            print(f'{row["id"]}. {row["title"]}\n{row["url"]}\nПрочитано символов: {len(row["text"])}')
        if args.answer:
            print(checker.answer(args.query, load_config()))
    except FactError as error:
        print('Проверка недоступна:', error)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
