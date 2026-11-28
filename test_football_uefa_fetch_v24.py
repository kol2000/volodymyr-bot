import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import football_uefa_fetch_v24 as v24


class V24Tests(unittest.TestCase):
    def test_validation_rejects_small_html(self):
        with self.assertRaisesRegex(ValueError, 'length'):
            v24.validate(b'bad', Path('/ignored'))

    def test_validate_delegates_to_verified_calendar(self):
        fake = ({n: [('fixture',)] * 18 for n in range(5, 9)}, {})
        with patch.object(v24.v22, 'verified_calendar', return_value=fake) as mock:
            counts, scores = v24.validate(b'x' * 10001, Path('fixtures.csv'))
        self.assertEqual(counts[8], 18)
        self.assertEqual(scores, 0)
        mock.assert_called_once()

    def test_wrong_count_rejected(self):
        fake = ({n: [('fixture',)] * (17 if n == 6 else 18) for n in range(5, 9)}, {})
        with patch.object(v24.v22, 'verified_calendar', return_value=fake):
            with self.assertRaisesRegex(ValueError, 'Wrong fixture counts'):
                v24.validate(b'x' * 10001, Path('fixtures.csv'))

    def test_atomic_save_and_metadata(self):
        with tempfile.TemporaryDirectory() as d:
            html = Path(d) / 'uefa.html'
            meta = Path(d) / 'info.json'
            html.write_bytes(b'old')
            raw = b'x' * 12000
            with patch.object(v24, 'validate', return_value=({n: 18 for n in range(5, 9)}, 0)):
                v24.cache_validated(raw, html, meta, Path('fixtures.csv'))
            self.assertEqual(html.read_bytes(), raw)
            self.assertIn(hashlib.sha256(raw).hexdigest(), meta.read_text())

    def test_failed_fetch_preserves_existing_cache(self):
        with tempfile.TemporaryDirectory() as d:
            html = Path(d) / 'uefa.html'
            html.write_bytes(b'last-verified')
            with patch.object(v24, 'fetch_once', side_effect=TimeoutError('timeout')):
                rc = v24.main(['--html', str(html), '--attempts', '1', '--timeout', '1'])
            self.assertEqual(rc, 2)
            self.assertEqual(html.read_bytes(), b'last-verified')

    def test_offline_check_never_writes(self):
        with tempfile.TemporaryDirectory() as d:
            html = Path(d) / 'uefa.html'
            html.write_bytes(b'x' * 12000)
            meta = Path(d) / 'meta.json'
            with patch.object(v24, 'validate', return_value=({n: 18 for n in range(5, 9)}, 0)):
                rc = v24.main(['--check', '--html', str(html), '--metadata', str(meta)])
            self.assertEqual(rc, 0)
            self.assertFalse(meta.exists())


if __name__ == '__main__':
    unittest.main()
