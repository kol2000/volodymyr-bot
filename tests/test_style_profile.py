import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from style_profile import corpus_profile, profile_for_request


class StyleProfileTests(unittest.TestCase):
    def test_measures_archive_but_sends_no_names_or_old_context(self):
        rows = [dict(text='ти провод проверь', context='Иван Секретный жаловался на интернет'),
                dict(text='коля дай денег', context='личная история'),
                dict(text='ну шо опять', context='разговор'),
                dict(text='ну шо опять', context='дубликат')]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'examples.jsonl'
            path.write_text('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows))
            with patch('style_profile.SOURCE', path):
                corpus_profile.cache_clear()
                profile = profile_for_request('интернет')
        self.assertEqual(profile['corpus_rows'], 4)
        self.assertEqual(profile['median_words'], 3)
        self.assertEqual(profile['examples'][0], 'ти провод проверь')
        text = json.dumps(profile, ensure_ascii=False)
        self.assertNotIn('Секретный', text)
        self.assertNotIn('коля', text)
        self.assertEqual(len(profile['examples']), 2)

    def test_missing_archive_degrades_to_static_style_without_network(self):
        with patch('style_profile.SOURCE', Path('/does-not-exist/examples.jsonl')):
            self.assertEqual(profile_for_request('почему')['examples'], [])

