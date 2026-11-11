from datetime import timedelta
from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from core.models import Organisation, KnowledgeSource, WebhookEvent, PaymentTransaction
from core.api import Problem
from core.services.knowledge import build_chunks, enqueue
from core.services.providers import ProviderFailure
from core.services.usage import recover_locked
from core.services.billing import reconcile

@shared_task
def process_source(org_id, source_id, revision):
    with transaction.atomic():
        Organisation.objects.select_for_update().get(pk=org_id)
        source = KnowledgeSource.objects.select_for_update().filter(pk=source_id,organisation_id=org_id,bot__organisation_id=org_id,revision=revision).first()
        if not source or source.status in ['ready','failed'] or (source.lease_until and source.lease_until>timezone.now()):
            return
        if source.attempts >= 3:
            source.status, source.error = 'failed', 'Processing retry limit reached. Edit the source to retry.'
            source.save()
            return
        source.status, source.lease_until = 'processing', timezone.now()+timedelta(seconds=settings.RESERVATION_SECONDS)
        source.attempts += 1
        source.save()
        attempt_number = source.attempts
    try:
        chunks = build_chunks(source)
        with transaction.atomic():
            row = KnowledgeSource.objects.select_for_update().filter(pk=source_id,organisation_id=org_id,revision=revision,attempts=attempt_number,status='processing').first()
            if not row:
                return
            row.chunks.all().delete()
            from core.models import DocumentChunk
            DocumentChunk.objects.bulk_create(chunks)
            row.status, row.error, row.lease_until = 'ready', '', None
            row.save()
    except Exception as exc:
        safe = str(exc.detail)[:240] if isinstance(exc,Problem) else 'Processing failed. Check provider configuration or PDF and edit to retry.'
        KnowledgeSource.objects.filter(pk=source_id,organisation_id=org_id,revision=revision,attempts=attempt_number).update(status='failed',error=safe,lease_until=None)

@shared_task
def process_payment(event_id):
    event = WebhookEvent.objects.select_related('payment').filter(pk=event_id).first()
    if not event or event.status in ['processed','ignored']:
        return
    try:
        reconcile(event.payment)
        WebhookEvent.objects.filter(pk=event.pk).update(status='processed',error='')
    except Problem as exc:
        WebhookEvent.objects.filter(pk=event.pk).update(status='failed',error='Verification unavailable or mismatched. Reconciliation required.')
        PaymentTransaction.objects.filter(pk=event.payment_id).update(error='Verification unavailable or mismatched.')

@shared_task
def recover():
    for org in Organisation.objects.iterator():
        with transaction.atomic():
            Organisation.objects.select_for_update().get(pk=org.pk)
            recover_locked(org)
    for source in KnowledgeSource.objects.filter(status='queued'):
        enqueue(process_source,str(source.organisation_id),str(source.pk),source.revision)
    for source in KnowledgeSource.objects.filter(status='processing',lease_until__lt=timezone.now()):
        # Crashed embedding calls are not automatically repeated; cost outcome is ambiguous.
        KnowledgeSource.objects.filter(pk=source.pk,status='processing',lease_until__lt=timezone.now()).update(
            status='failed',error='Worker lease expired. Provider outcome unknown; edit source to retry.',lease_until=None)
    for event in WebhookEvent.objects.filter(status='pending'):
        enqueue(process_payment,str(event.pk))
