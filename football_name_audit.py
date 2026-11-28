"""Report unseen caption spellings without automatically trusting new aliases."""
import logging
import re
from functools import lru_cache


def caption_lines(node):
    if isinstance(node, str):
        return node
    if node.tag == 'br':
        return '\n'
    return ''.join(caption_lines(child) for child in node.children)


def team_pairs(text):
    quoted = list(re.finditer(r'[\"«“]([^\"»”\n]+)[\"»”]\s*\d{1,2}\s*:\s*\d{1,2}\s*[\"«“]([^\"»”\n]+)[\"»”]', text))
    if quoted:
        for match in quoted:
            yield match[1].strip(), match[2].strip()
        return
    for line in text.splitlines():
        match = re.fullmatch(r'\s*(.{2,65}?)\s+(\d{1,2})\s*:\s*(\d{1,2})\s+(.{2,65}?)\s*', line)
        if match:
            pair = tuple(match[i].strip(' \"«»“”') for i in (1, 4))
            if all(not re.search(r'[\d@|,\"⚽]', name) for name in pair):
                yield pair


@lru_cache(maxsize=1)
def known_names():
    from goal_videos import norm, ALIASES
    from football_club_names import index
    return set(index(norm)) | {norm(s) for k, values in ALIASES.items() for s in (k, *values)}


@lru_cache(maxsize=4096)
def report_caption(text, link):
    from goal_videos import norm
    for pair in team_pairs(text):
        for name in pair:
            if norm(name) not in known_names():
                logging.getLogger('volodymyr').warning('unknown_football_name name=%r source=%s', name, link)
