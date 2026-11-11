from urllib.parse import urlsplit
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from core.models import User, Bot, KnowledgeSource, APIKey, Conversation, Message, Plan
from core.auth import SCOPES

class RegisterSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True, max_length=128)
    organisation_name = serializers.CharField(max_length=160)
    def validate_username(self,value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError('Username unavailable.')
        return value
    def validate_email(self,value):
        value = value.lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('Email unavailable.')
        return value
    def validate(self,data):
        from django.core.exceptions import ValidationError
        try:
            validate_password(data['password'],User(username=data['username'],email=data['email']))
        except ValidationError as exc:
            raise serializers.ValidationError({'password':exc.messages})
        return data

class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id','username','email']

class OrganisationSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    role = serializers.CharField()

class RegisterResultSerializer(serializers.Serializer):
    user = UserSerializer()
    organisation = OrganisationSerializer()
    access = serializers.CharField()
    refresh = serializers.CharField()

class BotSerializer(serializers.ModelSerializer):
    class Meta:
        model = Bot
        fields = ['id','name','description','allowed_origins','active','created_at']
        read_only_fields = ['id','created_at']
    def validate_allowed_origins(self,value):
        if not isinstance(value,list) or len(value)>20:
            raise serializers.ValidationError('Use a list of at most 20 exact origins.')
        for origin in value:
            if not isinstance(origin,str):
                raise serializers.ValidationError('Origins must be strings.')
            import re
            if not re.fullmatch(r'https?://[A-Za-z0-9.\-\[\]:]+', origin):
                raise serializers.ValidationError('Invalid origin characters.')
            parsed = urlsplit(origin)
            if (parsed.scheme not in (['http','https'] if settings.DEMO_MODE else ['https']) or not parsed.netloc
                or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment or '*' in origin):
                raise serializers.ValidationError('Use exact origins such as https://example.com, without path or wildcard.')
        return list(dict.fromkeys(value))

class SourceSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True,required=False)
    text = serializers.CharField(required=False,allow_blank=True,max_length=100000)
    class Meta:
        model = KnowledgeSource
        fields = ['id','title','kind','text','file','status','error','revision','size_bytes','created_at']
        read_only_fields = ['id','status','error','revision','size_bytes','created_at']

class KeyCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=100)
    bot_id = serializers.UUIDField(required=False,allow_null=True)
    scopes = serializers.ListField(child=serializers.ChoiceField(choices=sorted(SCOPES)),allow_empty=False)
    expires_at = serializers.DateTimeField(required=False,allow_null=True)
    def validate_expires_at(self,value):
        if value and value<=timezone.now():
            raise serializers.ValidationError('Expiry must be in the future.')
        return value

class KeySerializer(serializers.ModelSerializer):
    class Meta:
        model = APIKey
        fields = ['id','name','bot_id','prefix','scopes','expires_at','revoked_at','last_used_at','created_at']
class KeyIssuedSerializer(serializers.Serializer):
    key = KeySerializer()
    secret = serializers.CharField()

class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['id','role','content','citations','created_at']
class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ['id','bot_id','principal','created_at']
class ConversationDetailSerializer(ConversationSerializer):
    messages = MessageSerializer(many=True)
    class Meta(ConversationSerializer.Meta):
        fields = ConversationSerializer.Meta.fields+['messages']

class ChatSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=settings.MAX_MESSAGE_CHARS)
    conversation_id = serializers.UUIDField(required=False,allow_null=True)
class CitationSerializer(serializers.Serializer):
    source_id = serializers.UUIDField()
    title = serializers.CharField()
    chunk = serializers.IntegerField()
class ChatUsageSerializer(serializers.Serializer):
    billable_answer = serializers.BooleanField()
    input_tokens = serializers.IntegerField()
    output_tokens = serializers.IntegerField()
    cost_microusd = serializers.IntegerField()
    cost_is_upper_bound = serializers.BooleanField()
    mode = serializers.ChoiceField(choices=['local-demo-no-ai','live-provider'])
class ChatResultSerializer(serializers.Serializer):
    answer = serializers.CharField()
    conversation_id = serializers.UUIDField()
    sources = CitationSerializer(many=True)
    usage = ChatUsageSerializer()

class PlanSerializer(serializers.ModelSerializer):
    class Meta:
        model = Plan
        fields = ['slug','name','price_kobo','bot_limit','answer_limit','spend_limit_microusd']
class CheckoutSerializer(serializers.Serializer):
    plan = serializers.SlugField()
class CheckoutResultSerializer(serializers.Serializer):
    reference = serializers.CharField()
    authorization_url = serializers.URLField()
    billing_mode = serializers.CharField()
class VisitorCreateSerializer(serializers.Serializer):
    origin = serializers.URLField()
class VisitorResultSerializer(serializers.Serializer):
    token = serializers.CharField()
    expires_at = serializers.DateTimeField()
class UsageSerializer(serializers.Serializer):
    entitlement_id = serializers.UUIDField()
    plan = serializers.CharField()
    starts_at = serializers.DateTimeField()
    paid_through = serializers.DateTimeField()
    answer_limit = serializers.IntegerField()
    bot_limit = serializers.IntegerField()
    successful_answers = serializers.IntegerField()
    reserved_answers = serializers.IntegerField()
    provider_attempts = serializers.IntegerField()
    spent_microusd = serializers.IntegerField()
    reserved_microusd = serializers.IntegerField()
    spend_limit_microusd = serializers.IntegerField()
    billing_mode = serializers.CharField()
class HealthSerializer(serializers.Serializer):
    mode = serializers.CharField()

class EmptySerializer(serializers.Serializer):
    pass

class TokenResultSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField(required=False)
