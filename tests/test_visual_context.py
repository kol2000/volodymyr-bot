import io
import unittest
from unittest.mock import patch
from PIL import Image
import visual_context as v


class VisualTests(unittest.TestCase):
    def png(self):
        out=io.BytesIO();Image.new('RGB',(80,60),'red').save(out,format='PNG');return out.getvalue()

    def test_media_selection_and_reply(self):
        photo={'photo':[{'file_id':'small'},{'file_id':'large'}]}
        self.assertEqual(v.select(photo)['file_id'],'large')
        self.assertIsNone(v.request(photo,False))
        self.assertEqual(v.request({'reply_to_message':photo},True)['media']['file_id'],'large')
        self.assertEqual(v.select({'sticker':{'file_id':'x','is_video':True}})['kind'],'video')
        self.assertIsNone(v.select({'document':{'mime_type':'application/pdf'}}))

    def test_animated_sticker_is_honest(self):
        r=v.select({'sticker':{'is_animated':True,'thumbnail':{'file_id':'thumb'}}})
        self.assertEqual(r['file_id'],'thumb');self.assertIn('Движение',r['note'])
        self.assertEqual(v.select({'sticker':{'is_animated':True}})['kind'],'unavailable')

    def test_two_models_and_no_images_in_persona(self):
        calls=[]
        def call(url,payload,timeout):
            calls.append(payload)
            return {'message':{'content':'Красный квадрат' if len(calls)==1 else 'красный квадрат вижу'}}
        with patch.object(v,'download',return_value=self.png()):
            result=v.answer({'ollama_url':'http://test','model':'persona'},None,{'kind':'image'},'Что это?',call)
        self.assertIn('вижу',result)
        self.assertEqual([c['model'] for c in calls],['gemma3:4b','persona'])
        self.assertIn('images',calls[0]['messages'][1])
        self.assertNotIn('images',calls[1]['messages'][1])
        self.assertIn('visual_evidence',calls[1]['messages'][1]['content'])

    def test_bad_image_does_not_call_model(self):
        with patch.object(v,'download',return_value=b'broken'),patch.object(v,'http_json') as call:
            self.assertIn('не получилось',v.answer({},None,{'kind':'image'},'',call))
            call.assert_not_called()

    def test_download_rejects_oversize_before_network(self):
        with self.assertRaises(ValueError):v.download(None,{'file_size':v.LIMIT+1})

    def test_bot_routes_captionless_sticker_reply(self):
        import test_bot
        fixture=test_bot.BotTests();fixture.setUp()
        try:
            fixture.bind()
            update=fixture.update('',number=12)
            update['message'].update(sticker={'file_id':'sticker'},reply_to_message={'from':{'id':99}})
            fixture.bot.handle(update)
            job=fixture.bot.utility_jobs.get_nowait()
            self.assertEqual(job['utility']['kind'],'visual')
            self.assertEqual(job['utility']['media']['file_id'],'sticker')
        finally:fixture.tearDown()

    def test_bot_routes_named_picture_and_ignores_unaddressed(self):
        import test_bot
        fixture=test_bot.BotTests();fixture.setUp()
        try:
            fixture.bind()
            update=fixture.update('бубус посмотри',number=13)
            update['message']['photo']=[{'file_id':'photo'}]
            fixture.bot.handle(update)
            self.assertEqual(fixture.bot.utility_jobs.get_nowait()['utility']['kind'],'visual')
            update=fixture.update('',number=14)
            update['message']['photo']=[{'file_id':'photo'}]
            fixture.bot.handle(update)
            self.assertTrue(fixture.bot.utility_jobs.empty())
        finally:fixture.tearDown()


if __name__=='__main__':unittest.main()
