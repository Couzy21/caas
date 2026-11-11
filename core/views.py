from datetime import timedelta
import uuid
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction, IntegrityError, connection
from django.shortcuts import get_object_or_404, render
from django.utils import timezone
from django.views.decorators.clickjacking import xframe_options_exempt
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes
from core.models import *
from core.serializers import *
from core.auth import tenant, principal, issue_key, LoginThrottle, RegistrationThrottle, ChatThrottle, VisitorThrottle
from core.api import ok, Problem, Pagination
from core.schema import envelope
from core.services.usage import grant, active_period, totals, recover_locked
from core.services.knowledge import save_source
from core.services.chat import chat
from core.services.billing import checkout, receive_webhook


def paginated(request, queryset, serializer):
    paginator = Pagination()
    page = paginator.paginate_queryset(queryset.order_by('-created_at'),request)
    return paginator.get_paginated_response(serializer(page,many=True).data)


def bot_for(request, bot_id, admin=False, scope=None):
    org = tenant(request,admin=admin,scope=scope)
    qs = Bot.objects.filter(organisation=org)
    if isinstance(request.auth, APIKey) and request.auth.bot_id:
        qs = qs.filter(pk=request.auth.bot_id)
    return org,get_object_or_404(qs,pk=bot_id)


class RegisterView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [RegistrationThrottle]
    @extend_schema(request=RegisterSerializer,responses={201:envelope(RegisterResultSerializer)},auth=[])
    def post(self,request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            with transaction.atomic():
                plan = Plan.objects.filter(slug='trial',active=True).first()
                if not plan:
                    raise Problem('Plans have not been configured. Run seed_plans.',503)
                user = User.objects.create_user(username=data['username'],email=data['email'],password=data['password'])
                org = Organisation.objects.create(name=data['organisation_name'])
                Membership.objects.create(user=user,organisation=org,role='owner')
                grant(org,plan)
                token = RefreshToken.for_user(user)
        except IntegrityError:
            raise Problem('Username or email unavailable.',409) from None
        return ok({'user':UserSerializer(user).data,'organisation':{'id':str(org.pk),'name':org.name,'role':'owner'},
                   'access':str(token.access_token),'refresh':str(token)},'Registered.',201)

class LoginView(TokenObtainPairView):
    throttle_classes = [LoginThrottle]
    @extend_schema(request=TokenObtainPairSerializer,responses=envelope(TokenResultSerializer))
    def post(self,request,*args,**kwargs):
        response = super().post(request,*args,**kwargs)
        return ok(response.data,'Logged in.')
class RefreshView(TokenRefreshView):
    throttle_classes = [LoginThrottle]
    @extend_schema(request=TokenRefreshSerializer,responses=envelope(TokenResultSerializer))
    def post(self,request,*args,**kwargs):
        response = super().post(request,*args,**kwargs)
        return ok(response.data,'Token refreshed.')

@extend_schema(auth=[{'jwtAuth': []}])
class MeView(APIView):
    @extend_schema(responses=envelope(UserSerializer))
    def get(self,request):
        tenant(request)
        return ok(UserSerializer(request.user).data)
@extend_schema(auth=[{'jwtAuth': []}])
class OrganisationView(APIView):
    @extend_schema(responses=envelope(OrganisationSerializer))
    def get(self,request):
        org = tenant(request)
        role = Membership.objects.get(user=request.user,organisation=org).role
        return ok({'id':str(org.pk),'name':org.name,'role':role})

@extend_schema(auth=[{'jwtAuth': []}])
class BotListView(APIView):
    @extend_schema(responses=envelope(BotSerializer,many=True),operation_id="bots_list",parameters=[OpenApiParameter('page',int,description='Page number.'),OpenApiParameter('page_size',int,description='Rows per page, default 20, maximum 100.')])
    def get(self,request):
        return paginated(request,Bot.objects.filter(organisation=tenant(request)),BotSerializer)
    @extend_schema(request=BotSerializer,responses={201:envelope(BotSerializer)})
    def post(self,request):
        org = tenant(request,admin=True)
        data = BotSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            Organisation.objects.select_for_update().get(pk=org.pk)
            period = active_period(org)
            if Bot.objects.filter(organisation=org).count()>=period.entitlement.bot_limit:
                raise Problem('Bot quota reached.',402)
            bot = data.save(organisation=org)
        return ok(BotSerializer(bot).data,'Bot created.',201)

@extend_schema(auth=[{'jwtAuth': []}])
class BotDetailView(APIView):
    @extend_schema(responses=envelope(BotSerializer))
    def get(self,request,bot_id):
        _,bot = bot_for(request,bot_id)
        return ok(BotSerializer(bot).data)
    @extend_schema(request=BotSerializer,responses=envelope(BotSerializer))
    def patch(self,request,bot_id):
        _,bot = bot_for(request,bot_id,admin=True)
        data = BotSerializer(bot,data=request.data,partial=True)
        data.is_valid(raise_exception=True)
        data.save()
        return ok(data.data,'Bot updated.')
    @extend_schema(responses=envelope(EmptySerializer))
    def delete(self,request,bot_id):
        org,bot = bot_for(request,bot_id,admin=True)
        with transaction.atomic():
            Organisation.objects.select_for_update().get(pk=org.pk)
            bot.delete()
        return ok(message='Bot deleted.')

@extend_schema(auth=[{'jwtAuth': []}])
class SourceListView(APIView):
    @extend_schema(responses=envelope(SourceSerializer,many=True),operation_id="sources_list",parameters=[OpenApiParameter('page',int,description='Page number.'),OpenApiParameter('page_size',int,description='Rows per page, default 20, maximum 100.')])
    def get(self,request,bot_id):
        org,bot = bot_for(request,bot_id)
        return paginated(request,KnowledgeSource.objects.filter(organisation=org,bot=bot),SourceSerializer)
    @extend_schema(request=SourceSerializer,responses={202:envelope(SourceSerializer)})
    def post(self,request,bot_id):
        org,bot = bot_for(request,bot_id,admin=True)
        data = SourceSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        source = save_source(org,bot,data.validated_data)
        return ok(SourceSerializer(source).data,'Knowledge queued for processing.',202)

@extend_schema(auth=[{'jwtAuth': []}])
class SourceDetailView(APIView):
    def source(self,request,bot_id,source_id,admin=False):
        org,bot = bot_for(request,bot_id,admin=admin)
        return org,bot,get_object_or_404(KnowledgeSource,pk=source_id,organisation=org,bot=bot)
    @extend_schema(responses=envelope(SourceSerializer))
    def get(self,request,bot_id,source_id):
        _,_,source = self.source(request,bot_id,source_id)
        return ok(SourceSerializer(source).data)
    @extend_schema(request=SourceSerializer,responses={202:envelope(SourceSerializer)})
    def patch(self,request,bot_id,source_id):
        org,bot,source = self.source(request,bot_id,source_id,True)
        data = SourceSerializer(source,data=request.data,partial=True)
        data.is_valid(raise_exception=True)
        source = save_source(org,bot,data.validated_data,source)
        return ok(SourceSerializer(source).data,'Knowledge requeued; stale chunks removed.',202)
    @extend_schema(responses=envelope(EmptySerializer))
    def delete(self,request,bot_id,source_id):
        org,_,source = self.source(request,bot_id,source_id,True)
        with transaction.atomic():
            Organisation.objects.select_for_update().get(pk=org.pk)
            source.delete()
        return ok(message='Knowledge deleted.')

@extend_schema(auth=[{'jwtAuth': []}])
class KeyListView(APIView):
    @extend_schema(responses=envelope(KeySerializer,many=True),parameters=[OpenApiParameter('page',int,description='Page number.'),OpenApiParameter('page_size',int,description='Rows per page, default 20, maximum 100.')])
    def get(self,request):
        return paginated(request,APIKey.objects.filter(organisation=tenant(request,admin=True)),KeySerializer)
    @extend_schema(request=KeyCreateSerializer,responses={201:envelope(KeyIssuedSerializer)})
    def post(self,request):
        org = tenant(request,admin=True)
        data = KeyCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        bot_id = values.pop('bot_id',None)
        bot = get_object_or_404(Bot,organisation=org,pk=bot_id) if bot_id else None
        key,secret = issue_key(org,dict(values,bot=bot))
        return ok({'key':KeySerializer(key).data,'secret':secret},'Copy the secret now; it is shown only once.',201)

@extend_schema(auth=[{'jwtAuth': []}])
class KeyRevokeView(APIView):
    @extend_schema(request=None,responses=envelope(EmptySerializer))
    def post(self,request,key_id):
        org = tenant(request,admin=True)
        key = get_object_or_404(APIKey,organisation=org,pk=key_id)
        APIKey.objects.filter(pk=key.pk).update(revoked_at=timezone.now())
        return ok(message='API key revoked.')
@extend_schema(auth=[{'jwtAuth': []}])
class KeyRotateView(APIView):
    @extend_schema(request=None,responses={201:envelope(KeyIssuedSerializer)})
    def post(self,request,key_id):
        org = tenant(request,admin=True)
        with transaction.atomic():
            key = get_object_or_404(APIKey.objects.select_for_update(),organisation=org,pk=key_id)
            if key.revoked_at or (key.expires_at and key.expires_at<=timezone.now()):
                raise Problem('Key is already revoked or expired.',409)
            key.revoked_at = timezone.now()
            key.save(update_fields=['revoked_at'])
            fresh,secret = issue_key(org,{'name':key.name,'scopes':key.scopes,'bot':key.bot,'expires_at':key.expires_at})
        return ok({'key':KeySerializer(fresh).data,'secret':secret},'Key rotated; copy the new secret now.',201)

CHAT_PARAMS = [OpenApiParameter('Idempotency-Key',str,OpenApiParameter.HEADER,required=True,description='Unique per principal, at most 100 characters. Replays never call the provider again.')]
class ChatView(APIView):
    throttle_classes = [ChatThrottle]
    @extend_schema(request=ChatSerializer,responses={200:envelope(ChatResultSerializer),503:envelope(ChatResultSerializer)},parameters=CHAT_PARAMS)
    def post(self,request,bot_id):
        org,bot = bot_for(request,bot_id,scope='chat:write')
        return run_chat(request,org,bot,principal(request))

def run_chat(request,org,bot,identity):
    key = request.headers.get('Idempotency-Key','')
    if not key or len(key)>100:
        raise Problem('Supply an Idempotency-Key header of 1 to 100 characters.')
    data = ChatSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    payload = {**data.validated_data,'bot_id':str(bot.pk)}
    if payload.get('conversation_id'):
        payload['conversation_id'] = str(payload['conversation_id'])
    result,status = chat(org,bot,identity,payload,key)
    return Response(result,status=status)

class ConversationListView(APIView):
    def queryset(self,request):
        org = tenant(request,scope='conversations:read')
        qs = Conversation.objects.filter(organisation=org)
        if isinstance(request.auth,APIKey):
            qs = qs.filter(principal=principal(request))
            if request.auth.bot_id:
                qs = qs.filter(bot_id=request.auth.bot_id)
        return qs
    @extend_schema(responses=envelope(ConversationSerializer,many=True),operation_id="conversations_list",parameters=[OpenApiParameter('page',int,description='Page number.'),OpenApiParameter('page_size',int,description='Rows per page, default 20, maximum 100.')])
    def get(self,request):
        return paginated(request,self.queryset(request),ConversationSerializer)
class ConversationDetailView(ConversationListView):
    @extend_schema(responses=envelope(ConversationDetailSerializer))
    def get(self,request,conversation_id):
        conversation = get_object_or_404(self.queryset(request),pk=conversation_id)
        # Bound transcript to latest 100 messages; complete export is deferred.
        data = ConversationSerializer(conversation).data
        data['messages'] = MessageSerializer(list(reversed(list(conversation.messages.filter(organisation=conversation.organisation).order_by('-created_at')[:100]))),many=True).data
        return ok(data)

@extend_schema(auth=[{'jwtAuth': []}])
class UsageView(APIView):
    @extend_schema(responses=envelope(UsageSerializer))
    def get(self,request):
        org = tenant(request)
        with transaction.atomic():
            Organisation.objects.select_for_update().get(pk=org.pk)
            recover_locked(org)
            try:
                period = active_period(org)
            except Problem:
                entitlement = Entitlement.objects.filter(organisation=org).order_by('-ends_at').first()
                if not entitlement:
                    return ok(None,'No entitlement.')
                period = UsagePeriod.objects.get(entitlement=entitlement)
            e = period.entitlement
            return ok({'entitlement_id':str(e.pk),'plan':e.plan_id,'starts_at':e.starts_at,'paid_through':e.ends_at,
                'answer_limit':period.answer_limit,'bot_limit':e.bot_limit,'spend_limit_microusd':period.spend_limit_microusd,
                'billing_mode':'30-day access; manual renewal',**totals(period)})
class PlanListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    @extend_schema(responses=envelope(PlanSerializer,many=True),auth=[],parameters=[OpenApiParameter('page',int,description='Page number.'),OpenApiParameter('page_size',int,description='Rows per page, default 20, maximum 100.')])
    def get(self,request):
        paginator = Pagination()
        page = paginator.paginate_queryset(Plan.objects.filter(active=True).order_by('price_kobo'),request)
        return paginator.get_paginated_response(PlanSerializer(page,many=True).data)
@extend_schema(auth=[{'jwtAuth': []}])
class CheckoutView(APIView):
    @extend_schema(request=CheckoutSerializer,responses={201:envelope(CheckoutResultSerializer)})
    def post(self,request):
        org = tenant(request,admin=True)
        data = CheckoutSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        plan = get_object_or_404(Plan,slug=data.validated_data['plan'],active=True,price_kobo__gt=0)
        payment,url = checkout(org,plan,request.user.email)
        return ok({'reference':payment.reference,'authorization_url':url,'billing_mode':'30-day access; manual renewal'},'Checkout initialised. Access requires server verification.',201)
class WebhookView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = []
    @extend_schema(request=OpenApiTypes.OBJECT,responses=envelope(EmptySerializer),auth=[],description='Raw JSON signed by Paystack-Signature (or explicitly enabled legacy x-paystack-signature). Persist then verify asynchronously.')
    def post(self,request):
        receive_webhook(request.body,request.headers)
        return ok(message='Webhook received.')

class LiveView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    @extend_schema(responses=envelope(HealthSerializer),auth=[])
    def get(self,request):
        return ok({'mode':'local-demo-no-ai' if settings.DEMO_MODE else 'production'},'Alive.')
class ReadyView(LiveView):
    @extend_schema(responses={200:envelope(HealthSerializer),503:envelope(EmptySerializer)},auth=[])
    def get(self,request):
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT 1')
                if connection.vendor == 'postgresql':
                    cursor.execute("SELECT 1 FROM pg_extension WHERE extname='vector'")
                    if not cursor.fetchone():
                        raise ValueError()
            
            if not Plan.objects.filter(active=True).exists():
                raise ValueError()
            if not settings.DEMO_MODE:
                from redis import Redis
                Redis.from_url(settings.REDIS_URL,socket_connect_timeout=2,socket_timeout=2).ping()
        except Exception:
            return ok(message='Dependencies unavailable.',status=503)
        return ok({'mode':'local-demo-no-ai' if settings.DEMO_MODE else 'production'},'Ready.')

class VisitorSessionView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VisitorThrottle]
    @extend_schema(request=VisitorCreateSerializer,responses={201:envelope(VisitorResultSerializer)},auth=[])
    def post(self,request,bot_id):
        data = VisitorCreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        bot = get_object_or_404(Bot,pk=bot_id,active=True)
        origin = data.validated_data['origin']
        # Runs within our iframe; parent origin must match the browser Referer origin at iframe bootstrap.
        # Non-browser clients can spoof origins: rate limits and shared quotas remain mandatory.
        if origin not in bot.allowed_origins:
            raise Problem('Website origin is not allowed.',403)
        active_period(bot.organisation)
        session = VisitorSession.objects.create(organisation=bot.organisation,bot=bot,origin=origin,expires_at=timezone.now()+timedelta(minutes=15))
        token = signing.dumps({'session':str(session.pk),'bot':str(bot.pk)},salt='visitor')
        return ok({'token':token,'expires_at':session.expires_at},'Visitor session created.',201)
class WidgetChatView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = [VisitorThrottle]
    @extend_schema(request=ChatSerializer,responses=envelope(ChatResultSerializer),parameters=CHAT_PARAMS+[
        OpenApiParameter('X-Visitor-Token',str,OpenApiParameter.HEADER,required=True),
        OpenApiParameter('X-Website-Origin',str,OpenApiParameter.HEADER,required=True)],auth=[])
    def post(self,request,bot_id):
        try:
            payload = signing.loads(request.headers.get('X-Visitor-Token',''),salt='visitor',max_age=900)
            session = VisitorSession.objects.select_related('bot','organisation').get(pk=payload['session'],bot_id=bot_id,
                organisation_id=models.F('bot__organisation_id'),expires_at__gt=timezone.now())
            if payload.get('bot') != str(bot_id) or not session.bot.active:
                raise ValueError()
        except (signing.BadSignature,KeyError,ValueError,VisitorSession.DoesNotExist):
            raise Problem('Invalid or expired visitor session.',401) from None
        if request.headers.get('X-Website-Origin') != session.origin or session.origin not in session.bot.allowed_origins:
            raise Problem('Website origin is not allowed.',403)
        return run_chat(request,session.organisation,session.bot,'visitor:'+str(session.pk))

@xframe_options_exempt
def widget_frame(request,bot_id):
    from urllib.parse import urlsplit
    bot = get_object_or_404(Bot,pk=bot_id,active=True)
    origin = request.GET.get('origin','')
    referer = urlsplit(request.headers.get('Referer',''))
    referer_origin = f'{referer.scheme}://{referer.netloc}'
    if origin not in bot.allowed_origins or referer_origin != origin:
        return HttpResponse('Website origin is not allowed.',status=403)
    response = render(request,'widget/frame.html',{'bot':bot,'origin':origin})
    response['Content-Security-Policy'] = f"default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors {origin}; base-uri 'none'; form-action 'none'"
    response['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response['Cache-Control'] = 'no-store'
    return response

def widget_example(request):
    return render(request,'widget/example.html')
