import hashlib
import hmac
import secrets
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
from rest_framework.throttling import SimpleRateThrottle
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from .models import APIKey, Membership

SCOPES = {'chat:write', 'conversations:read'}

class KeyPrincipal:
    is_authenticated = True
    def __init__(self, key):
        self.key = key
        self.pk = key.id

class APIKeyAuthentication(BaseAuthentication):
    def authenticate(self, request):
        value = request.headers.get('Authorization', '')
        if not value.startswith('ApiKey '):
            return None
        token = value[7:]
        prefix = token.split('.')[0]
        key = APIKey.objects.filter(prefix=prefix, revoked_at__isnull=True).first()
        if not key or not hmac.compare_digest(key.secret_hash, hashlib.sha256(token.encode()).hexdigest()):
            raise AuthenticationFailed('Invalid API key.')
        if key.expires_at and key.expires_at <= timezone.now():
            raise AuthenticationFailed('API key expired.')
        APIKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())
        return KeyPrincipal(key), key
    def authenticate_header(self, request):
        return 'ApiKey'

class KeyScheme(OpenApiAuthenticationExtension):
    target_class = 'core.auth.APIKeyAuthentication'
    name = 'ServerAPIKey'
    def get_security_definition(self, auto_schema):
        return {'type': 'apiKey', 'in': 'header', 'name': 'Authorization', 'description': 'ApiKey <prefix.secret>. Server-side only.'}

def tenant(request, admin=False, scope=None):
    if isinstance(request.auth, APIKey):
        if admin or scope not in request.auth.scopes:
            raise PermissionDenied('This API key does not permit this operation.')
        return request.auth.organisation
    membership = Membership.objects.select_related('organisation').filter(user=request.user).first()
    if not membership:
        raise PermissionDenied('No organisation membership.')
    if admin and membership.role not in ['owner', 'admin']:
        raise PermissionDenied('Owner or admin role required.')
    return membership.organisation

def principal(request):
    return f'key:{request.auth.id}' if isinstance(request.auth, APIKey) else f'user:{request.user.id}'

def issue_key(org, data):
    token = f'caas_{secrets.token_hex(5)}.{secrets.token_urlsafe(32)}'
    key = APIKey.objects.create(organisation=org, prefix=token.split('.')[0],
                               secret_hash=hashlib.sha256(token.encode()).hexdigest(), **data)
    return key, token

class GeneralThrottle(SimpleRateThrottle):
    scope = 'general'
    def get_cache_key(self, request, view):
        user = request.user
        identity = str(user.pk) if user and user.is_authenticated else self.get_ident(request)
        return self.cache_format % {'scope': self.scope, 'ident': identity}
class LoginThrottle(GeneralThrottle):
    scope = 'login'
class RegistrationThrottle(GeneralThrottle):
    scope = 'registration'
class ChatThrottle(GeneralThrottle):
    scope = 'chat'
class VisitorThrottle(GeneralThrottle):
    scope = 'visitor'
