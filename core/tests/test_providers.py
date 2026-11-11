from unittest.mock import patch
from django.test import SimpleTestCase, override_settings
import httpx
from core.services import providers

@override_settings(DEMO_MODE=False,OPENAI_API_KEY='fixture-not-real',CHAT_MODEL='configured-chat',EMBEDDING_MODEL='configured-embedding')
class AdapterTests(SimpleTestCase):
    def test_embeddings_request_and_usage(self):
        with patch('core.services.providers.post',return_value={'data':[{'index':0,'embedding':[0.0]*1536}],'usage':{'total_tokens':8}}) as post:
            vectors,tokens=providers.embed(['hello'])
        self.assertEqual(tokens,8);self.assertEqual(len(vectors[0]),1536)
        self.assertEqual(post.call_args.args[0],'embeddings')
        self.assertEqual(post.call_args.args[1]['dimensions'],1536)
    def test_responses_request_and_usage(self):
        from types import SimpleNamespace
        result={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':'Delivery costs 2500.'}]}],
                'usage':{'input_tokens':25,'output_tokens':7}}
        with patch('core.services.providers.post',return_value=result) as post:
            text,inputs,outputs=providers.answer(SimpleNamespace(name='Store'),'Delivery?',[SimpleNamespace(text='fee 2500')],[])
        self.assertEqual((inputs,outputs),(25,7));self.assertIn('2500',text)
        payload=post.call_args.args[1]
        self.assertFalse(payload['store']);self.assertEqual(payload['max_output_tokens'],400)
        self.assertIn('untrusted',payload['instructions'])
    def test_timeout_is_not_retried_or_exposed(self):
        with patch('core.services.providers.httpx.Client') as client:
            request=client.return_value.__enter__.return_value.post
            request.side_effect=httpx.ReadTimeout('sensitive upstream details')
            with self.assertRaises(providers.ProviderFailure) as exc: providers.post('responses',{})
        self.assertEqual(request.call_count,1)
        self.assertNotIn('sensitive',str(exc.exception))
    def test_bad_response_is_rejected(self):
        with patch('core.services.providers.post',return_value={'data':[],'usage':{'total_tokens':0}}):
            with self.assertRaises(providers.ProviderFailure): providers.embed(['hi'])
        with patch('core.services.providers.post',return_value={'output':[],'status':'incomplete'}):
            from types import SimpleNamespace
            with self.assertRaises(providers.ProviderFailure): providers.answer(SimpleNamespace(name='x'),'x',[],[])
