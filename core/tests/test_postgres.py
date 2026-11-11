from unittest import skipUnless
from unittest.mock import patch
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone
from datetime import timedelta
from core.models import KnowledgeSource,DocumentChunk,ProviderAttempt
from core.tests.test_mvp import Fixtures
from core.services.knowledge import retrieve
from core.services.usage import recover_locked
from core.tasks import process_source,recover

@skipUnless(connection.vendor=='postgresql','Real pgvector retrieval requires PostgreSQL')
class VectorTests(Fixtures,TestCase):
    def setUp(self): self.setup_data()
    @override_settings(DEMO_MODE=False,EMBEDDING_MODEL='test-model')
    def test_real_vector_query_filters_both_bot_and_tenant(self):
        embedding=[1.0]+[0.0]*1535
        sources=[]
        for org,bot in [(self.org,self.bot),(self.other,self.other_bot)]:
            source=KnowledgeSource.objects.create(organisation=org,bot=bot,title='secret',kind='text',status='ready',text='business data')
            sources.append(source)
            DocumentChunk.objects.create(organisation=org,bot=bot,source=source,position=0,text='business data',embedding=embedding,embedding_model='test-model')
        # Deliberately inconsistent record must also be excluded by source tenant filters.
        DocumentChunk.objects.create(organisation=self.org,bot=self.bot,source=sources[1],position=1,text='secret',embedding=embedding,embedding_model='test-model')
        with patch('core.services.providers.embed',return_value=([embedding],3)):
            chunks,tokens=retrieve(self.org,self.bot,'hello')
        self.assertEqual(len(chunks),1)
        self.assertEqual(chunks[0].source_id,sources[0].pk)
        self.assertEqual(tokens,3)

@override_settings(DEMO_MODE=True,CELERY_TASK_ALWAYS_EAGER=True)
class ProcessingTests(Fixtures,TestCase):
    def setUp(self):self.setup_data()
    def test_failed_processing_edit_can_retry_without_stale_chunks(self):
        source=KnowledgeSource.objects.create(organisation=self.org,bot=self.bot,title='delivery',kind='text',text='Delivery fee is 2500')
        with patch('core.tasks.build_chunks',side_effect=RuntimeError('upstream secret')):
            process_source(str(self.org.pk),str(source.pk),1)
        source.refresh_from_db()
        self.assertEqual(source.status,'failed');self.assertNotIn('secret',source.error)
        process_source(str(self.org.pk),str(source.pk),1)
        self.assertEqual(source.chunks.count(),0)
        from core.services.knowledge import save_source
        with self.captureOnCommitCallbacks(execute=True):save_source(self.org,self.bot,{'text':'Delivery fee is 2500'},source)
        source.refresh_from_db();self.assertEqual(source.status,'ready');self.assertEqual(source.chunks.count(),1)
    def test_stale_worker_does_not_auto_repeat_embeddings(self):
        source=KnowledgeSource.objects.create(organisation=self.org,bot=self.bot,title='x',kind='text',text='delivery',status='processing',attempts=1,lease_until=timezone.now()-timedelta(seconds=1))
        attempt=ProviderAttempt.objects.create(organisation=self.org,period=self.period,operation='embedding',reserved_cost_microusd=55,expires_at=timezone.now()-timedelta(seconds=1))
        with patch('core.services.providers.embed') as provider:recover()
        provider.assert_not_called();source.refresh_from_db();attempt.refresh_from_db()
        self.assertEqual(source.status,'failed');self.assertEqual(attempt.status,'uncertain');self.assertEqual(attempt.cost_microusd,55)
