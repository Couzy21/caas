import hashlib
import hmac
import json
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from unittest import skipUnless
from unittest.mock import patch
from django.conf import settings
from django.core.cache import cache
from django.core.management import call_command
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection, connections, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from pypdf import PdfWriter
from core.models import *
from core.auth import issue_key
from core.tasks import process_source, recover, process_payment
from core.services.knowledge import save_source, extract_pdf, retrieve
from core.services.chat import reserve, chat
from core.services.providers import ProviderFailure
from core.services.usage import grant, active_period, totals, recover_locked
from core.services.billing import apply_verified, verify_signature
from core.api import Problem

PREFIX = '/api/v1/'

class Fixtures:
    def setup_data(self):
        cache.clear()
        call_command('seed_plans',verbosity=0)
        self.user = User.objects.create_user(username='alice',email='alice@example.test',password='a-strong-pass-123')
        self.org = Organisation.objects.create(name='Alice Shop')
        Membership.objects.create(user=self.user,organisation=self.org,role='owner')
        self.period = grant(self.org,Plan.objects.get(pk='trial'))
        self.bot = Bot.objects.create(organisation=self.org,name='Alice Bot',allowed_origins=['http://localhost:8000'])
        self.other_user = User.objects.create_user(username='bob',email='bob@example.test',password='a-strong-pass-123')
        self.other = Organisation.objects.create(name='Bob Shop')
        Membership.objects.create(user=self.other_user,organisation=self.other,role='owner')
        grant(self.other,Plan.objects.get(pk='trial'))
        self.other_bot = Bot.objects.create(organisation=self.other,name='Bob Bot')
        self.client = APIClient()
        self.client.force_authenticate(self.user)
    def knowledge(self,text='Delivery within Lagos costs 2500 naira and takes three days.'):
        with self.captureOnCommitCallbacks(execute=True):
            source = save_source(self.org,self.bot,{'kind':'faq','title':'Delivery','text':text})
        source.refresh_from_db()
        return source
    def post_chat(self,message='What is the delivery fee?',key='request-1',conversation=None,client=None,bot=None):
        data = {'message':message}
        if conversation:
            data['conversation_id'] = str(conversation)
        return (client or self.client).post(PREFIX+f'bots/{(bot or self.bot).pk}/chat/',data,format='json',HTTP_IDEMPOTENCY_KEY=key)
    def payment(self,reference=None):
        p = Plan.objects.get(pk='starter')
        return PaymentTransaction.objects.create(organisation=self.org,plan=p,reference=reference or uuid.uuid4().hex,
            email=self.user.email,amount_kobo=p.price_kobo,bot_limit=p.bot_limit,answer_limit=p.answer_limit,spend_limit_microusd=p.spend_limit_microusd)
    def verified(self,payment):
        return {'status':'success','reference':payment.reference,'amount':payment.amount_kobo,'currency':'NGN',
                'customer':{'email':payment.email},'metadata':{'organisation_id':str(payment.organisation_id),'plan':payment.plan_id}}

@override_settings(DEMO_MODE=True,CELERY_TASK_ALWAYS_EAGER=True)
class APITests(Fixtures,TestCase):
    def setUp(self):
        self.setup_data()

    def test_registration_login_refresh_and_password_validation(self):
        c = APIClient()
        response = c.post(PREFIX+'auth/register/',{'username':'new','email':'new@example.test','password':'strong-new-pass-654','organisation_name':'New'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(Membership.objects.get(user__username='new').role,'owner')
        login = c.post(PREFIX+'auth/login/',{'username':'new','password':'strong-new-pass-654'},format='json')
        self.assertEqual(login.status_code,200)
        self.assertIn('message',login.data)
        c.credentials(HTTP_AUTHORIZATION='Bearer '+login.data['data']['access'])
        self.assertEqual(c.get(PREFIX+'organisation/').status_code,200)
        refresh = c.post(PREFIX+'auth/refresh/',{'refresh':login.data['data']['refresh']},format='json')
        self.assertEqual(refresh.status_code,200)
        self.assertEqual(c.post(PREFIX+'auth/refresh/',{'refresh':login.data['data']['refresh']},format='json').status_code,401)
        c.credentials()
        response = c.post(PREFIX+'auth/register/',{'username':'short','email':'short@example.test','password':'123','organisation_name':'New'},format='json')
        self.assertEqual(response.status_code,400)
        self.assertEqual(c.post(PREFIX+'auth/login/',{'username':'new','password':'bad'},format='json').status_code,401)

    def test_bot_quota_permissions_and_tenant_isolation(self):
        self.assertEqual(self.client.get(PREFIX+f'bots/{self.other_bot.pk}/').status_code,404)
        self.assertEqual(self.client.patch(PREFIX+f'bots/{self.other_bot.pk}/',{'name':'attack'}).status_code,404)
        response = self.client.post(PREFIX+'bots/',{'name':'Over quota','organisation_id':str(self.other.pk)},format='json')
        self.assertEqual(response.status_code,402)
        Membership.objects.filter(user=self.user).update(role='member')
        self.assertEqual(self.client.post(PREFIX+'keys/',{'name':'x','scopes':['chat:write']},format='json').status_code,403)
        self.assertEqual(self.client.post(PREFIX+'billing/checkout/',{'plan':'starter'},format='json').status_code,403)
        self.assertEqual(self.client.delete(PREFIX+f'bots/{self.bot.pk}/').status_code,403)
        self.assertEqual(self.client.get(PREFIX+'bots/').data['count'],1)
        self.assertTrue(self.client.get(PREFIX+'bots/').data['results'])

    def test_keys_hash_scope_expiry_rotation_revocation(self):
        response = self.client.post(PREFIX+'keys/',{'name':'server','bot_id':str(self.bot.pk),'scopes':['chat:write']},format='json')
        self.assertEqual(response.status_code,201,response.data)
        secret = response.data['data']['secret']
        key = APIKey.objects.get(pk=response.data['data']['key']['id'])
        self.assertNotEqual(secret,key.secret_hash)
        self.assertNotIn(secret,self.client.get(PREFIX+'keys/').content.decode())
        c=APIClient();c.credentials(HTTP_AUTHORIZATION='ApiKey '+secret)
        self.assertEqual(c.get(PREFIX+'keys/').status_code,403)
        self.assertEqual(c.get(PREFIX+'conversations/').status_code,403)
        self.assertEqual(self.post_chat(client=c,bot=self.other_bot).status_code,404)
        self.assertEqual(self.post_chat(client=c).status_code,200)
        key.refresh_from_db();self.assertIsNotNone(key.last_used_at)
        fresh=self.client.post(PREFIX+f'keys/{key.pk}/rotate/',{},format='json')
        self.assertEqual(fresh.status_code,201)
        self.assertEqual(self.post_chat(client=c,key='next').status_code,401)
        c.credentials(HTTP_AUTHORIZATION='ApiKey '+fresh.data['data']['secret'])
        fresh_key=APIKey.objects.get(pk=fresh.data['data']['key']['id'])
        fresh_key.expires_at=timezone.now()-timedelta(seconds=1);fresh_key.save()
        self.assertEqual(self.post_chat(client=c,key='next').status_code,401)
        fresh_key.expires_at=None;fresh_key.save()
        self.assertEqual(self.client.post(PREFIX+f'keys/{fresh_key.pk}/revoke/').status_code,200)
        self.assertEqual(self.post_chat(client=c,key='next').status_code,401)

    def test_api_key_cannot_bind_other_tenant_bot(self):
        response=self.client.post(PREFIX+'keys/',{'name':'bad','bot_id':str(self.other_bot.pk),'scopes':['chat:write']},format='json')
        self.assertEqual(response.status_code,404)

    def test_ingestion_ready_retry_edit_and_delete(self):
        source = self.knowledge()
        self.assertEqual(source.status,'ready',source.error)
        before=source.chunks.count()
        process_source(str(self.org.pk),str(source.pk),source.revision)
        self.assertEqual(source.chunks.count(),before)
        process_source(str(self.other.pk),str(source.pk),source.revision)
        self.assertEqual(source.chunks.count(),before)
        with self.captureOnCommitCallbacks(execute=False):
            response=self.client.patch(PREFIX+f'bots/{self.bot.pk}/sources/{source.pk}/',{'text':'Returns are accepted within seven days.'},format='json')
        self.assertEqual(response.status_code,202)
        self.assertEqual(source.chunks.count(),0)
        process_source(str(self.org.pk),str(source.pk),1)
        self.assertEqual(source.chunks.count(),0)
        source.refresh_from_db()
        process_source(str(self.org.pk),str(source.pk),source.revision)
        self.assertEqual(source.chunks.count(),1)
        self.assertEqual(self.client.delete(PREFIX+f'bots/{self.bot.pk}/sources/{source.pk}/').status_code,200)
        self.assertEqual(DocumentChunk.objects.filter(source_id=source.pk).count(),0)

    def test_source_tenant_isolation(self):
        source=self.knowledge()
        c=APIClient();c.force_authenticate(self.other_user)
        self.assertEqual(c.get(PREFIX+f'bots/{self.bot.pk}/sources/{source.pk}/').status_code,404)
        self.assertEqual(c.delete(PREFIX+f'bots/{self.bot.pk}/sources/{source.pk}/').status_code,404)
        chunks,_=retrieve(self.other,self.other_bot,'delivery')
        self.assertEqual(chunks,[])

    def test_upload_rejects_non_pdf_encrypted_scanned_and_oversize(self):
        url=PREFIX+f'bots/{self.bot.pk}/sources/'
        for content in [b'not pdf',b'%PDF- corrupt']:
            response=self.client.post(url,{'title':'Bad','kind':'pdf','file':SimpleUploadedFile('bad.pdf',content)},format='multipart')
            self.assertEqual(response.status_code,400)
        writer=PdfWriter();writer.add_blank_page(width=100,height=100)
        stream=BytesIO();writer.write(stream)
        with self.assertRaises(Problem): extract_pdf(stream.getvalue())
        writer.encrypt('secret');stream=BytesIO();writer.write(stream)
        with self.assertRaises(Problem) as exc: extract_pdf(stream.getvalue())
        self.assertIn('Encrypted',str(exc.exception))
        with self.assertRaises(Problem): extract_pdf(b'%PDF-'+b'x'*settings.MAX_UPLOAD_BYTES)
        response=self.client.post(url,{'title':'x','kind':'text','text':'x'*100001},format='json')
        self.assertEqual(response.status_code,400)

    @override_settings(MAX_ORG_STORAGE_BYTES=5)
    def test_storage_limit(self):
        response=self.client.post(PREFIX+f'bots/{self.bot.pk}/sources/',{'kind':'text','title':'x','text':'too much text'},format='json')
        self.assertEqual(response.status_code,413)

    def test_missing_knowledge_free_and_message_limit(self):
        with patch('core.services.providers.answer') as provider:
            response=self.post_chat()
        provider.assert_not_called()
        self.assertEqual(response.status_code,200)
        self.assertIn('enough information',response.data['data']['answer'])
        self.assertFalse(response.data['data']['usage']['billable_answer'])
        self.assertEqual(self.post_chat(message='x'*2001,key='too-long').status_code,400)

    def test_chat_retrieval_citations_idempotency_and_payload_conflict(self):
        self.knowledge()
        first=self.post_chat()
        self.assertEqual(first.status_code,200,first.data)
        self.assertTrue(first.data['data']['usage']['billable_answer'])
        self.assertEqual(len(first.data['data']['sources']),1)
        with patch('core.services.providers.answer') as answer:
            second=self.post_chat()
        answer.assert_not_called()
        self.assertEqual(first.data,second.data)
        self.assertEqual(UsageRecord.objects.filter(billable=True).count(),1)
        self.assertEqual(Message.objects.count(),2)
        self.assertEqual(self.post_chat(message='Different',key='request-1').status_code,409)
        self.assertEqual(self.post_chat(message='Astronomy',key='unrelated').data['data']['sources'],[])

    def test_conversation_ownership_and_cross_tenant(self):
        first=self.post_chat()
        conversation=first.data['data']['conversation_id']
        c=APIClient();c.force_authenticate(self.other_user)
        self.assertEqual(c.get(PREFIX+f'conversations/{conversation}/').status_code,404)
        self.assertEqual(self.post_chat(client=c,bot=self.other_bot,conversation=conversation,key='other').status_code,404)
        user=User.objects.create_user(username='colleague',email='colleague@example.test')
        Membership.objects.create(user=user,organisation=self.org,role='member')
        c.force_authenticate(user)
        self.assertEqual(self.post_chat(client=c,conversation=conversation,key='same-org').status_code,404)
        self.assertEqual(self.client.get(PREFIX+f'conversations/{conversation}/').status_code,200)

    def test_expired_entitlement_retains_management(self):
        Entitlement.objects.filter(organisation=self.org).update(ends_at=timezone.now()-timedelta(seconds=1),starts_at=timezone.now()-timedelta(days=31))
        self.assertEqual(self.post_chat().status_code,402)
        for path in ['bots/','usage/','organisation/','keys/']:
            self.assertEqual(self.client.get(PREFIX+path).status_code,200,path)

    def test_quota_and_account_spending_limits(self):
        self.knowledge()
        self.period.answer_limit=1;self.period.save()
        self.assertEqual(self.post_chat().status_code,200)
        self.assertEqual(self.post_chat(key='second').status_code,429)
        self.assertEqual(self.post_chat().status_code,200)

    @override_settings(DEMO_MODE=False)
    def test_provider_failure_not_retried_and_reservation_released(self):
        source=KnowledgeSource.objects.create(organisation=self.org,bot=self.bot,title='x',kind='text',text='delivery',status='ready')
        DocumentChunk.objects.create(organisation=self.org,bot=self.bot,source=source,position=0,text='delivery',metadata={'title':'x'})
        with patch('core.services.chat.retrieve',side_effect=ProviderFailure()) as retrieve_mock:
            response=self.post_chat()
            replay=self.post_chat()
        self.assertEqual(response.status_code,503,response.data)
        self.assertEqual(replay.status_code,503)
        self.assertEqual(retrieve_mock.call_count,1)
        record=UsageRecord.objects.get()
        self.assertFalse(record.billable)
        self.assertTrue(record.provider_attempted)
        self.assertEqual(record.cost_microusd,record.reserved_cost_microusd)
        self.assertEqual(totals(self.period)['reserved_answers'],0)

    def test_crashed_reservation_recovery_never_reissues_provider(self):
        payload={'message':'delivery','bot_id':str(self.bot.pk)}
        record,_=reserve(self.org,self.bot,'user:'+str(self.user.pk),payload,'crashed')
        UsageRecord.objects.filter(pk=record.pk).update(expires_at=timezone.now()-timedelta(seconds=1),provider_attempted=True,reserved_cost_microusd=100)
        with transaction.atomic(): recover_locked(self.org)
        record.refresh_from_db()
        self.assertEqual(record.status,'uncertain')
        self.assertEqual(record.cost_microusd,100)
        with patch('core.services.providers.answer') as provider:
            response,status=chat(self.org,self.bot,'user:'+str(self.user.pk),payload,'crashed')
        provider.assert_not_called();self.assertEqual(status,503)

    def test_pending_idempotency_returns_conflict(self):
        payload={'message':'delivery','bot_id':str(self.bot.pk)}
        reserve(self.org,self.bot,'user:'+str(self.user.pk),payload,'pending')
        self.assertEqual(self.post_chat(message='delivery',key='pending').status_code,409)

    def test_widget_sessions_origin_scope_and_management_denial(self):
        c=APIClient()
        path=PREFIX+f'widget/{self.bot.pk}/session/'
        self.assertEqual(c.post(path,{'origin':'https://evil.example'},format='json').status_code,403)
        response=c.post(path,{'origin':'http://localhost:8000'},format='json')
        self.assertEqual(response.status_code,201)
        token=response.data['data']['token']
        headers={'HTTP_X_VISITOR_TOKEN':token,'HTTP_X_WEBSITE_ORIGIN':'http://localhost:8000','HTTP_IDEMPOTENCY_KEY':'widget'}
        url=PREFIX+f'widget/{self.bot.pk}/chat/'
        response=c.post(url,{'message':'hello'},format='json',**headers)
        self.assertEqual(response.status_code,200,response.data)
        headers['HTTP_X_WEBSITE_ORIGIN']='https://evil.example'
        self.assertEqual(c.post(url,{'message':'hello'},format='json',**headers).status_code,403)
        headers['HTTP_X_WEBSITE_ORIGIN']='http://localhost:8000'
        self.assertEqual(c.post(PREFIX+f'widget/{self.other_bot.pk}/chat/',{'message':'hello'},format='json',**headers).status_code,401)
        c.credentials(HTTP_AUTHORIZATION='Bearer '+token)
        self.assertEqual(c.get(PREFIX+'bots/').status_code,401)
        VisitorSession.objects.update(expires_at=timezone.now()-timedelta(seconds=1))
        c.credentials()
        self.assertEqual(c.post(url,{'message':'hello'},format='json',**headers).status_code,401)

    def test_widget_frame_enforces_referer_and_csp(self):
        url=f'/widget/{self.bot.pk}/?origin=http://localhost:8000'
        self.assertEqual(self.client.get(url,HTTP_REFERER='https://evil.example/').status_code,403)
        response=self.client.get(url,HTTP_REFERER='http://localhost:8000/widget-example/')
        self.assertEqual(response.status_code,200)
        self.assertIn('frame-ancestors http://localhost:8000',response['Content-Security-Policy'])
        self.assertNotIn('X-Frame-Options',response)

    @override_settings(DEMO_MODE=False)
    def test_spend_safeguard_prevents_provider_call(self):
        source=KnowledgeSource.objects.create(organisation=self.org,bot=self.bot,title='x',kind='text',text='x',status='ready')
        DocumentChunk.objects.create(organisation=self.org,bot=self.bot,source=source,position=0,text='x')
        self.period.spend_limit_microusd=1;self.period.save()
        with patch('core.services.providers.embed') as provider:
            response=self.post_chat()
        self.assertEqual(response.status_code,402)
        provider.assert_not_called()

    def test_bot_origin_validation(self):
        for origin in ['*','https://example.com/path','https://example.com;','https://user:pass@example.com']:
            response=self.client.patch(PREFIX+f'bots/{self.bot.pk}/',{'allowed_origins':[origin]},format='json')
            self.assertEqual(response.status_code,400,origin)

    def test_liveness_readiness_docs(self):
        for path in ['health/live/','health/ready/','schema/','docs/']:
            self.assertEqual(APIClient().get(PREFIX+path).status_code,200,path)

    def test_bot_delete_retains_idempotency_history(self):
        self.post_chat()
        self.client.delete(PREFIX+f'bots/{self.bot.pk}/')
        self.assertEqual(UsageRecord.objects.count(),1)

@override_settings(DEMO_MODE=True,CELERY_TASK_ALWAYS_EAGER=True,PAYSTACK_SECRET_KEY='sk_test_fixture',PAYSTACK_WEBHOOK_SECRET='whsec_fixture')
class PaymentTests(Fixtures,TestCase):
    def setUp(self): self.setup_data()
    def signed(self,payment,event_type='charge.success',legacy=False):
        body=json.dumps({'event':event_type,'data':{'reference':payment.reference}}).encode()
        t=str(int(time.time()))
        if legacy:
            return body,{'HTTP_X_PAYSTACK_SIGNATURE':hmac.new(settings.PAYSTACK_SECRET_KEY.encode(),body,hashlib.sha512).hexdigest()}
        signature=hmac.new(settings.PAYSTACK_WEBHOOK_SECRET.encode(),t.encode()+b'.'+body,hashlib.sha512).hexdigest()
        return body,{'HTTP_PAYSTACK_SIGNATURE':f't={t},v1=whk_test:{signature}'}
    def webhook(self,body,headers):
        return APIClient().post(PREFIX+'billing/paystack/webhook/',body,content_type='application/json',**headers)

    def test_signatures_invalid_tampered_stale_and_legacy(self):
        p=self.payment();body,headers=self.signed(p)
        self.assertEqual(self.webhook(body,{}).status_code,401)
        self.assertEqual(self.webhook(body+b' ',headers).status_code,401)
        with patch('core.services.billing.time.time',return_value=time.time()+400):
            self.assertEqual(self.webhook(body,headers).status_code,401)
        body,headers=self.signed(p,legacy=True)
        self.assertEqual(self.webhook(body,headers).status_code,401)
        with override_settings(PAYSTACK_ALLOW_LEGACY_SIGNATURE=True):
            with patch('core.services.billing.paystack',return_value=self.verified(p)),self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(self.webhook(body,headers).status_code,200)

    def test_valid_duplicate_webhook_grants_exactly_once(self):
        p=self.payment();body,headers=self.signed(p)
        with patch('core.services.billing.paystack',return_value=self.verified(p)) as provider:
            self.assertEqual(self.webhook(body,headers).status_code,200)
            self.assertEqual(self.webhook(body,headers).status_code,200)
        p.refresh_from_db()
        self.assertEqual(p.status,'succeeded',p.error)
        self.assertEqual(WebhookEvent.objects.count(),1)
        self.assertEqual(provider.call_count,1)
        self.assertEqual(Entitlement.objects.filter(organisation=self.org,plan_id='starter').count(),1)
        self.assertEqual(active_period(self.org).entitlement_id,p.entitlement_id)

    def test_wrong_amount_currency_customer_org_reference_not_granted(self):
        for name,value in [('amount',123),('currency','USD'),('reference','other'),('customer',{'email':'attacker@example.test'}),('metadata',{'organisation_id':str(self.other.pk),'plan':'starter'})]:
            p=self.payment();data=self.verified(p);data[name]=value
            with self.assertRaises(Problem): apply_verified(p,data)
            p.refresh_from_db();self.assertIsNone(p.entitlement_id,name)

    def test_webhook_persists_failed_state_and_reconciliation(self):
        p=self.payment();body,headers=self.signed(p)
        with patch('core.services.billing.paystack',side_effect=Problem('Unavailable',503)):
            self.assertEqual(self.webhook(body,headers).status_code,200)
        event=WebhookEvent.objects.get();self.assertEqual(event.status,'failed')
        with patch('core.services.billing.paystack',return_value=self.verified(p)):
            call_command('reconcile_payments',reference=p.reference,verbosity=0)
        p.refresh_from_db();event.refresh_from_db()
        self.assertIsNotNone(p.entitlement_id);self.assertEqual(event.status,'processed')

    def test_out_of_order_events_preserve_access_and_never_reset_period(self):
        p=self.payment();apply_verified(p,self.verified(p))
        period=active_period(self.org);original_end=period.entitlement.ends_at
        body,headers=self.signed(p,'subscription.disable')
        self.assertEqual(self.webhook(body,headers).status_code,200)
        apply_verified(p,self.verified(p))
        self.assertEqual(active_period(self.org).pk,period.pk)
        self.assertEqual(active_period(self.org).entitlement.ends_at,original_end)

    def test_manual_renewal_schedules_new_period_and_preserves_paid_through(self):
        p=self.payment();p=apply_verified(p,self.verified(p))
        first=UsagePeriod.objects.get(entitlement=p.entitlement)
        p2=self.payment();p2=apply_verified(p2,self.verified(p2))
        self.assertEqual(p2.entitlement.starts_at,p.entitlement.ends_at)
        self.assertEqual(active_period(self.org).pk,first.pk)
        self.assertEqual(first.answer_limit,500)
        with patch('django.utils.timezone.now',return_value=p2.entitlement.starts_at+timedelta(seconds=1)):
            self.assertEqual(active_period(self.org).entitlement_id,p2.entitlement_id)
        with patch('django.utils.timezone.now',return_value=p2.entitlement.ends_at+timedelta(seconds=1)):
            with self.assertRaises(Problem): active_period(self.org)

    def test_checkout_uses_server_price_and_metadata(self):
        with patch('core.services.billing.paystack') as provider:
            provider.side_effect=lambda m,p,d:{'reference':d['reference'],'authorization_url':'https://checkout.paystack.com/test'}
            response=self.client.post(PREFIX+'billing/checkout/',{'plan':'starter','amount':1,'organisation_id':str(self.other.pk)},format='json')
        self.assertEqual(response.status_code,201,response.data)
        payload=provider.call_args.args[2]
        self.assertEqual(payload['amount'],1500000)
        self.assertEqual(payload['metadata']['organisation_id'],str(self.org.pk))
        self.assertEqual(Entitlement.objects.filter(organisation=self.org).count(),1)

@skipUnless(connection.vendor=='postgresql','PostgreSQL required for real row-lock concurrency')
@override_settings(DEMO_MODE=True,CELERY_TASK_ALWAYS_EAGER=True)
class ConcurrentQuotaTests(Fixtures,TransactionTestCase):
    def setUp(self): self.setup_data()
    def test_two_requests_cannot_reserve_last_slot(self):
        self.period.answer_limit=1;self.period.save()
        org_id,bot_id=self.org.pk,self.bot.pk
        import threading
        barrier=threading.Barrier(2)
        def run(index):
            connections.close_all()
            try:
                org=Organisation.objects.get(pk=org_id);bot=Bot.objects.get(pk=bot_id)
                barrier.wait(timeout=10)
                try:
                    reserve(org,bot,'thread:'+str(index),{'message':'hello','bot_id':str(bot_id)},'same')
                    return 'reserved'
                except Problem as exc:
                    return exc.status_code
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(run,[1,2]))
        self.assertCountEqual(results,['reserved',429])
        self.assertEqual(UsageRecord.objects.filter(status='reserved').count(),1)

    def test_same_idempotency_key_concurrent_reserves_only_once(self):
        import threading
        barrier=threading.Barrier(2)
        org_id,bot_id=self.org.pk,self.bot.pk
        def run(index):
            connections.close_all()
            try:
                org=Organisation.objects.get(pk=org_id);bot=Bot.objects.get(pk=bot_id)
                barrier.wait(timeout=10)
                try:
                    reserve(org,bot,'same-principal',{'message':'hello','bot_id':str(bot_id)},'same-key')
                    return 'reserved'
                except Problem as exc: return exc.status_code
            finally: connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(run,[1,2]))
        self.assertCountEqual(results,['reserved',409])
        self.assertEqual(UsageRecord.objects.count(),1)

@override_settings(DEMO_MODE=True,CELERY_TASK_ALWAYS_EAGER=True)
class AdditionalSafeguardTests(Fixtures,TestCase):
    def setUp(self): self.setup_data()
    def test_auth_and_chat_throttles_apply(self):
        from core.auth import LoginThrottle, RegistrationThrottle, ChatThrottle
        for route,klass in [('auth/login/',LoginThrottle),('auth/register/',RegistrationThrottle)]:
            cache.clear()
            with patch.object(klass,'rate','1/min',create=True):
                c=APIClient();c.post(PREFIX+route,{},format='json')
                response=c.post(PREFIX+route,{},format='json')
                self.assertEqual(response.status_code,429,route)
        cache.clear()
        with patch.object(ChatThrottle,'rate','1/min',create=True):
            self.assertEqual(self.post_chat(key='first').status_code,200)
            self.assertEqual(self.post_chat(key='second').status_code,429)
    def test_plan_downgrade_does_not_leave_excess_bots_usable(self):
        extra=Bot.objects.create(organisation=self.org,name='excess')
        self.assertEqual(self.post_chat(bot=extra).status_code,402)
        self.assertEqual(self.client.get(PREFIX+f'bots/{extra.pk}/').status_code,200)
    def test_valid_text_pdf_upload_processes_and_private_file_is_removed(self):
        from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
        writer=PdfWriter();page=writer.add_blank_page(width=600,height=800)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 700 Td (Delivery in Lagos costs 2500 naira and takes three days.) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        output=BytesIO();writer.write(output)
        with self.captureOnCommitCallbacks(execute=True):
            response=self.client.post(PREFIX+f'bots/{self.bot.pk}/sources/',{'title':'PDF FAQ','kind':'pdf','file':SimpleUploadedFile('delivery.pdf',output.getvalue(),content_type='application/pdf')},format='multipart')
        self.assertEqual(response.status_code,202,response.data)
        source=KnowledgeSource.objects.get(pk=response.data['data']['id']);self.assertEqual(source.status,'ready',source.error)
        self.assertTrue(source.file.storage.exists(source.file.name));name=source.file.name;storage=source.file.storage
        self.assertNotIn('file',response.data['data'])
        with self.captureOnCommitCallbacks(execute=True):source.delete()
        self.assertFalse(storage.exists(name))
    def test_deleted_conversation_preserves_charge_audit(self):
        self.post_chat()
        Conversation.objects.all().delete()
        self.assertEqual(UsageRecord.objects.count(),1)
