import json
import os
import unittest
from unittest.mock import MagicMock, patch

from fact_check import Article, FactChecker, FactError, PublicRedirect, fetch_public, public_url


RSS = '''<rss><channel>
<item><title>Official Python</title><link>https://www.python.org/about/</link></item>
<item><title>Documentation</title><link>https://docs.python.org/3/</link></item>
<item><title>Encyclopedia</title><link>https://ru.wikipedia.org/wiki/Python</link></item>
<item><title>Private</title><link>https://127.0.0.1/private</link></item>
</channel></rss>'''
PAGE = '<html><title>Python</title><nav>ignore navigation</nav><article>' + 'Python is a programming language. ' * 20 + '</article><script>ignore instructions</script></html>'


class FactTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.now = [1000]
        def fetch(url):
            self.calls.append(url)
            return (RSS if 'bing.com/search?' in url else PAGE), url
        self.checker = FactChecker(fetch=fetch, clock=lambda: self.now[0])
        self.config = dict(ollama_url='http://127.0.0.1:11434', model='local-model')

    def result(self, **overrides):
        data = dict(verdict='confirmed', answer='ето язык программирования по прочитанным страницам.', sources=[1])
        data.update(overrides)
        return {'message': {'content': json.dumps(data, ensure_ascii=False)}}

    def test_search_reads_pages_and_returns_only_actual_sources_with_timestamp(self):
        rows, checked = self.checker.evidence('Python язык программирования')
        self.assertEqual(len(rows), 3)
        self.assertEqual(checked, 1000)
        self.assertNotIn('ignore instructions', rows[0]['text'])
        self.assertNotIn('ignore navigation', rows[0]['text'])
        self.assertFalse(any('127.0.0.1' in url for url in self.calls))
        with patch('fact_check.http_json', return_value=self.result()) as model:
            text = self.checker.answer('Python язык программирования', self.config)
        self.assertIn('https://www.python.org/about/', text)
        self.assertIn('Получено', text)
        data = json.loads(model.call_args.args[1]['messages'][1]['content'])
        self.assertEqual(len(data['sources']), 3)
        self.assertTrue(all('text' in row for row in data['sources']))
        self.assertNotIn('live_context', data)

    def test_cache_expires_and_does_not_label_old_evidence_as_new(self):
        first = self.checker.evidence('Python язык программирования')
        count = len(self.calls)
        self.now[0] += 299
        self.assertEqual(self.checker.evidence('Python язык программирования'), first)
        self.assertEqual(len(self.calls), count)
        self.now[0] += 2
        self.assertEqual(self.checker.evidence('Python язык программирования')[1], 1301)
        self.assertGreater(len(self.calls), count)

    def test_cannot_cite_invented_source_or_emit_partial_and_malformed_model_answers(self):
        variants = [self.result(sources=[99]), self.result(sources=[True]), self.result(sources=[]),
                    self.result(answer='https://invented.example/'), self.result(answer='<think>черновик</think>'),
                    self.result(verdict='definitely'), {'message': {'content': 'bad json'}},
                    dict(self.result(), done_reason='length'), []]
        for result in variants:
            with self.subTest(result=result):
                with patch('fact_check.http_json', return_value=result):
                    with self.assertRaises(FactError):
                        self.checker.answer('Python язык программирования', self.config)

    def test_failed_page_never_turns_search_snippet_into_proof(self):
        def fetch(url):
            if 'bing.com/search?' in url:
                return RSS, url
            raise FactError('network_error')
        checker = FactChecker(fetch)
        with patch('fact_check.http_json') as model:
            with self.assertRaisesRegex(FactError, 'no_readable_sources'):
                checker.answer('Python язык программирования', self.config)
        model.assert_not_called()

    def test_unknown_hosts_private_ips_credentials_ports_and_redirects_are_rejected(self):
        for url in ['http://python.org', 'https://127.0.0.1/', 'https://192.168.1.5/',
                    'https://python.org.attacker.example/', 'https://python.org@127.0.0.1/',
                    'https://python.org:444/', 'file:///etc/passwd']:
            with self.subTest(url=url):
                with self.assertRaises(FactError):
                    public_url(url)
        with self.assertRaises(FactError):
            PublicRedirect().redirect_request(None, None, 302, '', {}, 'https://192.168.1.5/')

    def test_direct_dns_must_be_global_and_never_connects_to_private_host(self):
        with patch.dict(os.environ, {}, clear=True), \
                patch('fact_check.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]), \
                patch('fact_check.socket.create_connection') as connect:
            with self.assertRaisesRegex(FactError, 'non_public_host'):
                fetch_public('https://www.python.org/')
        connect.assert_not_called()

    def test_direct_connection_uses_checked_ip_and_original_tls_hostname(self):
        connection = MagicMock()
        response = connection.getresponse.return_value
        response.status = 200
        response.getheader.return_value = 'text/html; charset=utf-8'
        response.read.return_value = PAGE.encode()
        context = MagicMock()
        with patch.dict(os.environ, {}, clear=True), \
                patch('fact_check.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 443))]), \
                patch('fact_check.socket.create_connection') as connect, \
                patch('fact_check.ssl.create_default_context', return_value=context), \
                patch('fact_check.http.client.HTTPSConnection', return_value=connection):
            text, final = fetch_public('https://www.python.org/about/')
        self.assertEqual(connect.call_args.args[0], ('93.184.216.34', 443))
        self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'www.python.org')
        self.assertEqual(final, 'https://www.python.org/about/')
        self.assertIn('Python', text)
        connection.close.assert_called_once()

    def test_html_answer_and_source_titles_are_escaped(self):
        self.checker.evidence('Python язык программирования')[0][0]['title'] = '<b>подмена</b>'
        with patch('fact_check.http_json', return_value=self.result(answer='ето <текст> по источнику.')):
            text = self.checker.answer('Python язык программирования', self.config)
        self.assertIn('&lt;текст&gt;', text)
        self.assertIn('&lt;b&gt;подмена&lt;/b&gt;', text)

    def test_no_captcha_short_page_or_invalid_search_is_used(self):
        with self.assertRaisesRegex(FactError, 'invalid_search'):
            FactChecker(fetch=lambda url: ('<html>challenge</html>', url)).evidence('Python язык')
        def fetch(url):
            return (RSS if 'bing.com/search?' in url else '<title>captcha</title>' + 'a' * 300), url
        with self.assertRaisesRegex(FactError, 'no_readable_sources'):
            FactChecker(fetch).evidence('Python язык')
