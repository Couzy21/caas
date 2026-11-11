from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.db.models import Sum, Count, Q
from django.utils import timezone
from core.models import Organisation, Entitlement, UsagePeriod, UsageRecord, ProviderAttempt
from core.api import Problem


def active_period(org):
    now = timezone.now()
    entitlement = Entitlement.objects.filter(organisation=org, starts_at__lte=now, ends_at__gt=now).order_by('-starts_at').first()
    if not entitlement:
        raise Problem('Access expired. Purchase 30 days of access; management remains available.', 402)
    return UsagePeriod.objects.get(entitlement=entitlement)


def grant(org, plan, start=None, terms=None):
    start = start or timezone.now()
    terms = terms or {'bot_limit': plan.bot_limit, 'answer_limit': plan.answer_limit, 'spend_limit_microusd': plan.spend_limit_microusd}
    entitlement = Entitlement.objects.create(organisation=org, plan=plan, bot_limit=terms['bot_limit'], starts_at=start, ends_at=start + timedelta(days=30))
    return UsagePeriod.objects.create(organisation=org, entitlement=entitlement, answer_limit=terms['answer_limit'], spend_limit_microusd=terms['spend_limit_microusd'])


def recover_locked(org):
    # A timed-out attempt may have cost money. Release the answer slot, retain the entire cost bound,
    # and never retry the same idempotency key. A late worker cannot commit an answer.
    stale = UsageRecord.objects.filter(organisation=org, status='reserved', expires_at__lt=timezone.now())
    for r in stale:
        r.status = 'uncertain' if r.provider_attempted else 'failed'
        r.cost_microusd = r.reserved_cost_microusd if r.provider_attempted else 0
        r.response = {'message': 'Request expired; provider outcome may be unknown. This key will not call the provider again.'}
        r.save(update_fields=['status','cost_microusd','response'])
    for r in ProviderAttempt.objects.filter(organisation=org, status='reserved', expires_at__lt=timezone.now()):
        r.status = 'uncertain'
        r.cost_microusd = r.reserved_cost_microusd
        r.save(update_fields=['status','cost_microusd'])


def totals(period):
    records = UsageRecord.objects.filter(period=period)
    costs = records.aggregate(spent=Sum('cost_microusd'), reserved=Sum('reserved_cost_microusd', filter=Q(status='reserved')))
    attempts = ProviderAttempt.objects.filter(period=period).aggregate(spent=Sum('cost_microusd'), reserved=Sum('reserved_cost_microusd', filter=Q(status='reserved')))
    return {'successful_answers': records.filter(billable=True).count(), 'reserved_answers': records.filter(status='reserved').count(),
            'provider_attempts': records.filter(provider_attempted=True).count() + ProviderAttempt.objects.filter(period=period).count(),
            'spent_microusd': (costs['spent'] or 0)+(attempts['spent'] or 0),
            'reserved_microusd': (costs['reserved'] or 0)+(attempts['reserved'] or 0)}


def check_budget(period, cost, answer=False):
    usage = totals(period)
    if answer and usage['successful_answers'] + usage['reserved_answers'] >= period.answer_limit:
        raise Problem('Successful-answer quota exhausted.', 429)
    if cost > settings.REQUEST_COST_MICROUSD:
        raise Problem('Request exceeds configured spending safeguard.', 402)
    if usage['spent_microusd'] + usage['reserved_microusd'] + cost > period.spend_limit_microusd:
        raise Problem('Account spending safeguard reached.', 402)


@transaction.atomic
def reserve_embedding(org, bound):
    Organisation.objects.select_for_update().get(pk=org.pk)
    recover_locked(org)
    period = active_period(org)
    check_budget(period, bound)
    return ProviderAttempt.objects.create(organisation=org, period=period, operation='embedding', reserved_cost_microusd=bound,
                                          expires_at=timezone.now()+timedelta(seconds=settings.RESERVATION_SECONDS))


@transaction.atomic
def finish_embedding(attempt, cost=None, tokens=0):
    Organisation.objects.select_for_update().get(pk=attempt.organisation_id)
    row = ProviderAttempt.objects.select_for_update().get(pk=attempt.pk)
    if row.status != 'reserved':
        raise Problem('Embedding reservation expired.', 409)
    row.status = 'succeeded' if cost is not None else 'uncertain'
    row.cost_microusd = row.reserved_cost_microusd if cost is None else cost
    row.tokens = tokens
    row.save()
