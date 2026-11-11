import hashlib
import json
from datetime import timedelta
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from core.api import Problem
from core.models import Organisation, Conversation, Message, UsageRecord
from .usage import active_period, recover_locked, check_budget
from .knowledge import retrieve
from . import providers


def request_bound(question, history):
    # JSON escaping can expand context; reserve using a conservative full serialized input ceiling.
    return (50000 * settings.INPUT_MICROUSD_PER_TOKEN + settings.MAX_OUTPUT_TOKENS * settings.OUTPUT_MICROUSD_PER_TOKEN
            + len(question.encode()) * settings.EMBED_MICROUSD_PER_TOKEN)


@transaction.atomic
def reserve(org, bot, principal, payload, key):
    Organisation.objects.select_for_update().get(pk=org.pk)
    recover_locked(org)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    prior = UsageRecord.objects.filter(organisation=org, principal=principal, idempotency_key=key).first()
    if prior:
        if prior.payload_hash != digest:
            raise Problem('Idempotency key was already used with a different payload.', 409)
        if prior.status == 'reserved':
            raise Problem('Request is still processing. Retry the same key later.', 409)
        return prior, None
    period = active_period(org)
    if not bot.active:
        raise Problem('Bot is disabled.', 403)
    from core.models import Bot
    permitted_ids = list(Bot.objects.filter(organisation=org).order_by('created_at','id')
                         .values_list('id',flat=True)[:period.entitlement.bot_limit])
    if bot.pk not in permitted_ids:
        raise Problem('This bot exceeds the current plan limit. Delete excess bots or purchase a larger plan.',402)
    conversation = None
    if payload.get('conversation_id'):
        conversation = Conversation.objects.filter(pk=payload['conversation_id'], organisation=org, bot=bot, principal=principal).first()
        if not conversation:
            raise Problem('Conversation not found.', 404)
        if UsageRecord.objects.filter(conversation=conversation, status='reserved').exists():
            raise Problem('Another request is processing in this conversation.', 409)
    history = []
    if conversation:
        for m in reversed(list(conversation.messages.order_by('-created_at')[:6])):
            history.append({'role': m.role, 'content': m.content[:600]})
    has_knowledge = bot.sources.filter(organisation=org, status='ready', chunks__isnull=False).exists()
    bound = 0 if settings.DEMO_MODE or not has_knowledge else request_bound(payload['message'], history)
    check_budget(period, bound, answer=True)
    if not conversation:
        conversation = Conversation.objects.create(organisation=org, bot=bot, principal=principal)
    record = UsageRecord.objects.create(organisation=org, period=period, conversation=conversation, principal=principal,
        idempotency_key=key, payload_hash=digest, reserved_cost_microusd=bound,
        expires_at=timezone.now()+timedelta(seconds=settings.RESERVATION_SECONDS))
    return record, history


@transaction.atomic
def finish(record, question, content, citations, inputs, outputs, embed_tokens, failed=False):
    Organisation.objects.select_for_update().get(pk=record.organisation_id)
    row = UsageRecord.objects.select_for_update().get(pk=record.pk)
    if row.status != 'reserved' or row.expires_at <= timezone.now():
        recover_locked(row.organisation)
        raise Problem('Request reservation expired. Retry this key to inspect its outcome.', 409)
    row.status = 'failed' if failed else 'succeeded'
    row.billable = not failed and bool(citations)
    row.input_tokens, row.output_tokens = inputs + embed_tokens, outputs
    if failed and row.provider_attempted:
        row.cost_microusd = row.reserved_cost_microusd
    elif not settings.DEMO_MODE:
        row.cost_microusd = inputs*settings.INPUT_MICROUSD_PER_TOKEN + outputs*settings.OUTPUT_MICROUSD_PER_TOKEN + embed_tokens*settings.EMBED_MICROUSD_PER_TOKEN
    row.response = {'message': 'Provider unavailable; no successful answer charged.' if failed else 'Answer ready.',
        'data': {'answer': content, 'conversation_id': str(row.conversation_id), 'sources': citations,
                 'usage': {'billable_answer': row.billable, 'input_tokens': row.input_tokens, 'output_tokens': outputs,
                           'cost_microusd': row.cost_microusd, 'cost_is_upper_bound': failed,
                           'mode': 'local-demo-no-ai' if settings.DEMO_MODE else 'live-provider'}}}
    if not failed:
        Message.objects.create(organisation=row.organisation, conversation=row.conversation, role='user', content=question)
        Message.objects.create(organisation=row.organisation, conversation=row.conversation, role='assistant', content=content, citations=citations)
    row.save()
    return row


def chat(org, bot, principal, payload, key):
    record, history = reserve(org, bot, principal, payload, key)
    if history is None:
        return record.response, 200 if record.status == 'succeeded' else 503
    inputs = outputs = embed_tokens = 0
    try:
        has_knowledge = bot.sources.filter(organisation=org, status='ready', chunks__isnull=False).exists()
        if has_knowledge and not settings.DEMO_MODE:
            # Mark before any network I/O so crashes retain a worst-case provider cost.
            UsageRecord.objects.filter(pk=record.pk, status='reserved').update(provider_attempted=True)
        chunks, embed_tokens = retrieve(org, bot, payload['message']) if has_knowledge else ([],0)
        if chunks:
            content, inputs, outputs = providers.answer(bot, payload['message'], chunks, history)
            citations = [{'source_id':str(c.source_id), 'title':c.metadata['title'], 'chunk':c.position} for c in chunks]
        else:
            content, citations = 'I do not have enough information in the supplied knowledge to answer that question.', []
            if settings.DEMO_MODE:
                content = '[LOCAL DEMO — no live AI] ' + content
        record = finish(record, payload['message'], content, citations, inputs, outputs, embed_tokens)
        return record.response, 200
    except providers.ProviderFailure:
        record = finish(record, payload['message'], '', [], 0, 0, 0, failed=True)
        return record.response, 503
