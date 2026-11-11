import hashlib
import hmac
import json
import time
import uuid
from django.conf import settings
from django.db import transaction
from django.utils import timezone
import httpx
from core.api import Problem
from core.models import Organisation, PaymentTransaction, Entitlement, WebhookEvent
from .usage import grant


def paystack(method, path, data=None):
    if not settings.PAYSTACK_SECRET_KEY:
        raise Problem('Paystack is not configured.', 503)
    try:
        with httpx.Client(timeout=httpx.Timeout(15, connect=5), transport=httpx.HTTPTransport(retries=0)) as client:
            response = client.request(method, 'https://api.paystack.co/' + path,
                headers={'Authorization': 'Bearer ' + settings.PAYSTACK_SECRET_KEY}, json=data)
            response.raise_for_status()
            body = response.json()
            if body.get('status') is not True:
                raise ValueError()
            return body['data']
    except (httpx.HTTPError, ValueError, KeyError):
        raise Problem('Payment provider unavailable. Retry later or contact support.', 503) from None


def checkout(org, plan, email):
    if not settings.PAYSTACK_SECRET_KEY:
        raise Problem('Paystack is not configured. Local demo does not simulate a real checkout.', 503)
    payment = PaymentTransaction.objects.create(organisation=org, plan=plan, email=email,
        reference='caas_' + uuid.uuid4().hex, amount_kobo=plan.price_kobo, bot_limit=plan.bot_limit,
        answer_limit=plan.answer_limit, spend_limit_microusd=plan.spend_limit_microusd)
    try:
        data = paystack('POST', 'transaction/initialize', {'reference':payment.reference, 'email':email,
            'amount':payment.amount_kobo, 'currency':'NGN',
            'metadata': {'organisation_id':str(org.id), 'plan':plan.slug}})
        if data.get('reference') != payment.reference or not str(data.get('authorization_url','')).startswith('https://'):
            raise Problem('Invalid payment provider response.', 503)
        return payment, data['authorization_url']
    except Problem:
        PaymentTransaction.objects.filter(pk=payment.pk).update(error='Checkout outcome unknown; reconcile reference before retrying.')
        raise


def verify_signature(body, headers):
    header = headers.get('Paystack-Signature')
    if header:
        if not settings.PAYSTACK_WEBHOOK_SECRET:
            return False
        fields = [p.strip().split('=',1) for p in header.split(',') if '=' in p]
        timestamps = [v for k,v in fields if k == 't']
        if len(timestamps) != 1 or not timestamps[0].isdigit():
            return False
        t = timestamps[0]
        if abs(time.time()-int(t)) > 300:
            return False
        expected = hmac.new(settings.PAYSTACK_WEBHOOK_SECRET.encode(), t.encode()+b'.'+body, hashlib.sha512).hexdigest()
        return any(hmac.compare_digest(expected, v.split(':',1)[1]) for k,v in fields
                   if k == 'v1' and ':' in v and len(v.split(':',1)[1]) == 128)
    if settings.PAYSTACK_ALLOW_LEGACY_SIGNATURE and settings.PAYSTACK_SECRET_KEY:
        signature = headers.get('x-paystack-signature','')
        expected = hmac.new(settings.PAYSTACK_SECRET_KEY.encode(),body,hashlib.sha512).hexdigest()
        return hmac.compare_digest(expected, signature)
    return False


def reconcile(payment):
    # Verification GET is safe to repeat; access grant is serialized by organisation and payment.
    data = paystack('GET', 'transaction/verify/' + payment.reference)
    return apply_verified(payment, data)


@transaction.atomic
def apply_verified(payment, data):
    Organisation.objects.select_for_update().get(pk=payment.organisation_id)
    payment = PaymentTransaction.objects.select_for_update().get(pk=payment.pk)
    if payment.entitlement_id:
        return payment
    metadata = data.get('metadata')
    if not isinstance(metadata, dict):
        metadata = {}
    valid = (data.get('status') == 'success' and data.get('reference') == payment.reference
        and type(data.get('amount')) is int and data['amount'] == payment.amount_kobo
        and data.get('currency') == payment.currency
        and str((data.get('customer') or {}).get('email','')).lower() == payment.email.lower()
        and metadata.get('organisation_id') == str(payment.organisation_id)
        and metadata.get('plan') == payment.plan_id)
    if not valid:
        # Save outside the transaction that raises, through reconciliation caller.
        raise Problem('Payment verification did not match the recorded purchase.', 400)
    now = timezone.now()
    # Buying paid access ends an active trial immediately; already-paid access is preserved.
    Entitlement.objects.filter(organisation=payment.organisation,plan_id='trial',starts_at__lt=now,ends_at__gt=now).update(ends_at=now)
    latest = Entitlement.objects.filter(organisation=payment.organisation).exclude(plan_id='trial').order_by('-ends_at').first()
    start = max(timezone.now(), latest.ends_at) if latest else timezone.now()
    # An upgrade is scheduled after existing access, avoiding quota resets or overlapping periods.
    period = grant(payment.organisation, payment.plan, start=start, terms={
        'bot_limit':payment.bot_limit,'answer_limit':payment.answer_limit,'spend_limit_microusd':payment.spend_limit_microusd})
    payment.entitlement, payment.status, payment.error = period.entitlement, 'succeeded', ''
    payment.save()
    return payment


def receive_webhook(body, headers):
    if not verify_signature(body,headers):
        raise Problem('Invalid payment signature.', 401)
    try:
        data = json.loads(body)
        event_type = data['event']
        reference = data['data']['reference']
        if not isinstance(event_type,str) or len(event_type)>80 or not isinstance(reference,str):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise Problem('Malformed webhook event.') from None
    payment = PaymentTransaction.objects.filter(reference=reference).first()
    if not payment:
        # Unrelated transactions cannot be assigned to an arbitrary tenant.
        return None
    event, _ = WebhookEvent.objects.get_or_create(digest=hashlib.sha256(body).hexdigest(), defaults={
        'organisation':payment.organisation,'payment':payment,'event_type':event_type})
    if event.status != 'processed':
        if event_type == 'charge.success':
            from core.tasks import process_payment
            from .knowledge import enqueue
            enqueue(process_payment, str(event.pk))
        else:
            WebhookEvent.objects.filter(pk=event.pk).update(status='ignored')
    return event
