"""Offline Azerbaijani club names linked to Russian names by article identity."""
import json
import logging
import re
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).with_name('football_club_names_az.json')


def variants(row):
    names = {row['ru'], row['az'], *row.get('aliases', ())}
    ru = row['ru']
    match = re.fullmatch(r'(.+?) \(футбольный клуб(?:, (.+))?\)', ru)
    if match:
        # Preserve a city/year qualifier: Dynamo Moscow is not Dynamo Kyiv.
        names.add(match[1] + (' ' + match[2] if match[2] else ''))
    az = row['az']
    names.add(re.sub(r'\s+(?:FK|PFK|FC|SK|JK)$', '', az))
    return names


def build_index(rows, normalize):
    owners = {}
    by_id = {}
    for row in rows:
        identity = row['id']
        words = {normalize(name) for name in variants(row)}
        words = {name for name in words if len(name) >= 3}
        by_id.setdefault(identity, set()).update(words)
        for name in words:
            owners.setdefault(name, set()).add(identity)
    # Ambiguous aliases cannot select a club or match a caption by themselves.
    return {name: tuple(sorted(alias for alias in by_id[next(iter(ids))]
                               if len(owners[alias]) == 1))
            for name, ids in owners.items() if len(ids) == 1}


@lru_cache(maxsize=1)
def index(normalize):
    try:
        data = json.loads(DATA.read_text(encoding='utf-8'))
        return build_index(data['clubs'], normalize)
    except (OSError, ValueError, KeyError, TypeError):
        logging.getLogger('volodymyr').warning('Словарь названий клубов недоступен; используются встроенные названия')
        return {}


def aliases(name, normalize):
    return index(normalize).get(normalize(name), ())
