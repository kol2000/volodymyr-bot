"""Tournament exclusions shared by selection, notifications and deferred jobs."""
import re


def excluded_competition(competition):
    name = ' '.join(str(competition or '').casefold().replace('ё', 'е').split())
    # FNL-2 is a different tournament; it is not Russia's First League.
    if re.search(r'\bфнл\s*[-–—]?\s*2\b', name) and 'первая лига' not in name:
        return False
    first = re.search(r'\b(?:первая лига|первый дивизион|1[ -]?[яй] (?:лига|дивизион)|'
                      r'лига (?:pari|пари)|фнл|first league)\b', name)
    if not first:
        return False
    # An unqualified Russian heading on FNK (including the screenshot's
    # "Первая лига, 15-й тур") denotes the domestic tournament. An explicit
    # foreign country before the title must not be excluded.
    return bool(re.search(r'\b(?:росси[яи]|russia)\b', name) or re.fullmatch(
        r'^(?:(?:мелбет|melbet)\s*[-–—.:]?\s*)?(?:первая лига|первый дивизион|'
        r'1[ -]?[яй] (?:лига|дивизион)|лига (?:pari|пари)|фнл)'
        r'(?:\s*[.,:–—-]?\s*(?:\d+[-–]?[йя]?\s*тур|тур\s*\d+|\(\s*фнл\s*\)))?\.?', name))
