import uuid
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower
from pgvector.django import VectorField

class User(AbstractUser):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True)
    class Meta:
        constraints = [models.UniqueConstraint(Lower('email'), name='unique_lower_user_email')]

class Entity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        abstract = True

class Organisation(Entity):
    name = models.CharField(max_length=160)

class Owned(Entity):
    organisation = models.ForeignKey(Organisation, on_delete=models.CASCADE)
    class Meta:
        abstract = True

class Membership(Owned):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    role = models.CharField(max_length=10, choices=[(r, r) for r in ['owner', 'admin', 'member']])
    class Meta:
        constraints = [models.UniqueConstraint(fields=['organisation', 'user'], name='unique_membership'),
                       models.UniqueConstraint(fields=['user'], name='one_org_per_user_mvp')]

class Plan(models.Model):
    slug = models.SlugField(primary_key=True)
    name = models.CharField(max_length=60)
    price_kobo = models.PositiveIntegerField()
    bot_limit = models.PositiveIntegerField()
    answer_limit = models.PositiveIntegerField()
    spend_limit_microusd = models.PositiveIntegerField(default=1000000)
    active = models.BooleanField(default=True)

class Entitlement(Owned):
    bot_limit = models.PositiveIntegerField(default=1)
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField(db_index=True)
    class Meta:
        indexes = [models.Index(fields=['organisation', 'starts_at', 'ends_at'])]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=models.F('starts_at')), name='positive_entitlement')]

class UsagePeriod(Owned):
    entitlement = models.OneToOneField(Entitlement, on_delete=models.CASCADE)
    answer_limit = models.PositiveIntegerField()
    spend_limit_microusd = models.PositiveIntegerField()
    # Limits are snapshotted for each purchased period.

class Bot(Owned):
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=500, blank=True)
    allowed_origins = models.JSONField(default=list)
    active = models.BooleanField(default=True)

class KnowledgeSource(Owned):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE, related_name='sources')
    title = models.CharField(max_length=160)
    kind = models.CharField(max_length=10, choices=[(x,x) for x in ['faq','text','pdf']])
    text = models.TextField(blank=True)
    file = models.FileField(upload_to='knowledge/%Y/%m/', blank=True)
    size_bytes = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, default='queued', choices=[(x,x) for x in ['queued','processing','ready','failed']])
    error = models.CharField(max_length=240, blank=True)
    revision = models.PositiveIntegerField(default=1)
    lease_until = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveIntegerField(default=0)
    class Meta:
        indexes = [models.Index(fields=['organisation', 'bot', 'status'])]

class DocumentChunk(Owned):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE)
    source = models.ForeignKey(KnowledgeSource, on_delete=models.CASCADE, related_name='chunks')
    position = models.PositiveIntegerField()
    text = models.TextField()
    embedding = VectorField(dimensions=1536, null=True)
    embedding_model = models.CharField(max_length=100, default='demo-lexical')
    metadata = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['source','position'], name='unique_source_chunk')]
        indexes = [models.Index(fields=['organisation','bot'])]

class APIKey(Owned):
    bot = models.ForeignKey(Bot, null=True, blank=True, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    prefix = models.CharField(max_length=20, unique=True)
    secret_hash = models.CharField(max_length=64)
    scopes = models.JSONField(default=list)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

class VisitorSession(Owned):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE)
    origin = models.URLField()
    expires_at = models.DateTimeField()

class Conversation(Owned):
    bot = models.ForeignKey(Bot, on_delete=models.CASCADE)
    principal = models.CharField(max_length=100)
    class Meta:
        indexes = [models.Index(fields=['organisation','bot','principal','created_at'])]

class Message(Owned):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10)
    content = models.TextField()
    citations = models.JSONField(default=list)
    class Meta:
        ordering = ['created_at', 'id']

class UsageRecord(Owned):
    period = models.ForeignKey(UsagePeriod, on_delete=models.PROTECT)
    conversation = models.ForeignKey(Conversation, on_delete=models.SET_NULL, null=True)
    principal = models.CharField(max_length=100)
    idempotency_key = models.CharField(max_length=100)
    payload_hash = models.CharField(max_length=64)
    status = models.CharField(max_length=16, default='reserved')
    expires_at = models.DateTimeField()
    provider_attempted = models.BooleanField(default=False)
    billable = models.BooleanField(default=False)
    reserved_cost_microusd = models.PositiveIntegerField(default=0)
    cost_microusd = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    response = models.JSONField(default=dict)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['organisation','principal','idempotency_key'], name='unique_chat_request')]
        indexes = [models.Index(fields=['period','status']), models.Index(fields=['status','expires_at'])]

class ProviderAttempt(Owned):
    period = models.ForeignKey(UsagePeriod, on_delete=models.PROTECT)
    operation = models.CharField(max_length=20)
    status = models.CharField(max_length=16, default='reserved')
    reserved_cost_microusd = models.PositiveIntegerField()
    cost_microusd = models.PositiveIntegerField(default=0)
    tokens = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField()

class PaymentTransaction(Owned):
    plan = models.ForeignKey(Plan, on_delete=models.PROTECT)
    reference = models.CharField(max_length=100, unique=True)
    email = models.EmailField()
    amount_kobo = models.PositiveIntegerField()
    currency = models.CharField(max_length=3, default='NGN')
    status = models.CharField(max_length=20, default='pending')
    error = models.CharField(max_length=120, blank=True)
    entitlement = models.OneToOneField(Entitlement, null=True, blank=True, on_delete=models.PROTECT)
    # Purchase terms are immutable, even when the plan is later edited.
    bot_limit = models.PositiveIntegerField()
    answer_limit = models.PositiveIntegerField()
    spend_limit_microusd = models.PositiveIntegerField()

class WebhookEvent(Owned):
    digest = models.CharField(max_length=64, unique=True)
    payment = models.ForeignKey(PaymentTransaction, on_delete=models.CASCADE)
    event_type = models.CharField(max_length=80)
    status = models.CharField(max_length=20, default='pending')
    error = models.CharField(max_length=120, blank=True)
