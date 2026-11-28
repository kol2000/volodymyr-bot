import json,unittest
from unittest.mock import patch,MagicMock
import link_reader as l
from fact_check import FactError,fetch_public,public_url

PAGE='<html><head><title>Статья</title></head><nav>лишнее</nav><article><p>'+('В статье описан запуск новой программы. '*12)+'</p></article></html>'
class LinkTests(unittest.TestCase):
    def test_routes_visible_hidden_reply(self):
        self.assertIsNone(l.request({'text':'https://example.org'},False))
        self.assertEqual(l.request({'reply_to_message':{'entities':[{'type':'text_link','url':'https://example.org/a'}]}},True)['urls'],['https://example.org/a'])
        self.assertEqual(l.request({},False,'/read')['kind'],'fixed')
        self.assertIsNone(l.request({'text':'https://example.org'},True,'/football'))
        self.assertEqual(l.urls({'text':'см https://example.org/a_(b).'}),['https://example.org/a_(b)'])
    def test_url_safety(self):
        for u in ['https://127.0.0.1','https://[::1]','https://169.254.169.254','https://user:pass@example.org','https://example.org:8080','file:///etc/passwd','https://foo.local']:
            with self.subTest(u=u),self.assertRaises((FactError,ValueError)):l.public_url(u)
        self.assertEqual(l.public_url('https://example.org/новость').hostname,'example.org')
        with self.assertRaises(FactError):public_url('https://example.org')
    def test_dns_private_block_even_proxy(self):
        with patch('fact_check.socket.getaddrinfo',return_value=[(2,1,6,'',('10.0.0.1',443))]),patch('fact_check.socket.create_connection') as conn,patch.dict('os.environ',{'HTTPS_PROXY':'https://proxy.example'}):
            with self.assertRaises(FactError):fetch_public('https://example.org',validator=l.public_url)
            conn.assert_not_called()
    def test_redirect_private_block(self):
        response=MagicMock();response.status=302;response.getheader.return_value='https://127.0.0.1/secret'
        connection=MagicMock();connection.getresponse.return_value=response
        with patch('fact_check.socket.getaddrinfo',return_value=[(2,1,6,'',('93.184.216.34',443))]),patch('fact_check.socket.create_connection'),patch('fact_check.ssl.create_default_context'),patch('fact_check.http.client.HTTPSConnection',return_value=connection):
            with self.assertRaises(FactError):fetch_public('https://example.org',validator=l.public_url)
            self.assertEqual(connection.request.call_count,1)
    def test_extract(self):
        d=l.load('https://example.org',lambda u,**kw:(PAGE,u))
        self.assertEqual(d['title'],'Статья');self.assertNotIn('лишнее',d['text'])
        with self.assertRaises(FactError):l.Article('<title>Just a moment</title>'+PAGE).document()
    def test_answer_uses_content_and_source(self):
        calls=[];utility={'urls':['https://example.org']}
        def model(u,p,**kw):calls.append(p);return {'message':{'content':'Суть публикации\n\n<важно>'}}
        answer=l.answer({'model':'test','ollama_url':'http://local'},utility,'Объясни',fetch=lambda u,**kw:(PAGE,u),call=model)
        self.assertIn('&lt;важно&gt;',answer);self.assertIn('https://example.org',answer)
        self.assertIn('новой программы',calls[0]['messages'][1]['content'])
        self.assertIn('Независимая проверка',answer);self.assertTrue(utility['_documents'])
    def test_failed_fetch_never_calls_model(self):
        model=MagicMock()
        def bad(*a,**k):raise FactError('denied')
        self.assertIn('не удалось',l.answer({},dict(urls=['https://example.org']), 'что там',fetch=bad,call=model));model.assert_not_called()
    def test_cache_scoped_and_followup(self):
        import test_bot
        f=test_bot.BotTests();f.setUp()
        try:
            docs=[{'text':'test','url':'https://example.org'}];l.cache_put(f.store,-100,7,docs)
            self.assertEqual(l.cache_get(f.store,-100,7),docs)
            self.assertIsNone(l.cache_get(f.store,-101,7))
            self.assertEqual(l.request({'text':'а смысл'},True,context=docs)['documents'],docs)
        finally:f.tearDown()
    def test_bot_route(self):
        import test_bot
        f=test_bot.BotTests();f.setUp()
        try:
            f.bind();f.bot.pending_requests.clear()
            f.bot.handle(f.update('/read https://example.org',number=10))
            job=f.bot.utility_jobs.get_nowait();self.assertEqual(job['utility']['kind'],'links')
        finally:f.tearDown()

    def test_bot_natural_request_and_cached_reply(self):
        import test_bot
        f=test_bot.BotTests();f.setUp()
        try:
            f.bind();f.bot.pending_requests.clear()
            f.bot.handle(f.update('бубус объясни https://example.org',number=11))
            job=f.bot.utility_jobs.get_nowait();self.assertEqual(job['utility']['kind'],'links')
            f.bot.pending_requests.clear();f.store.set('request:-100',0)
            docs=[{'text':'test','url':'https://example.org'}];l.cache_put(f.store,-100,777,docs)
            update=f.update('а почему это важно?',number=12)
            update['message']['reply_to_message']={'message_id':777,'from':{'id':99,'is_bot':True},'text':'Разбор публикации'}
            f.bot.handle(update)
            job=f.bot.utility_jobs.get_nowait();self.assertEqual(job['utility']['documents'],docs)
        finally:f.tearDown()

    def test_summarize_article_priority_and_chat_command(self):
        import test_bot
        for text,reply,expected in [
            ('бубус перескажи ету статью: https://oreltimes.ru/news/test/',None,'links'),
            ('бубус перескажи эту статью',{'text':'https://example.org'},'links'),
            ('/summary https://example.org',None,'summary'),
            ('бубус перескажи чат',None,'summary')]:
            with self.subTest(text=text):
                f=test_bot.BotTests();f.setUp()
                try:
                    f.bind();f.bot.pending_requests.clear()
                    update=f.update(text,number=30)
                    if reply:update['message']['reply_to_message']=reply
                    f.bot.handle(update)
                    queue=f.bot.jobs if expected=='summary' else f.bot.utility_jobs
                    self.assertEqual(queue.get_nowait()['utility']['kind'],expected)
                finally:f.tearDown()
