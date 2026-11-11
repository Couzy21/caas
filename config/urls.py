from django.contrib import admin
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from core import views as v

urlpatterns = [
    path('admin/',admin.site.urls),
    path('api/v1/auth/register/',v.RegisterView.as_view()),
    path('api/v1/auth/login/',v.LoginView.as_view()),
    path('api/v1/auth/refresh/',v.RefreshView.as_view()),
    path('api/v1/me/',v.MeView.as_view()),
    path('api/v1/organisation/',v.OrganisationView.as_view()),
    path('api/v1/bots/',v.BotListView.as_view()),
    path('api/v1/bots/<uuid:bot_id>/',v.BotDetailView.as_view()),
    path('api/v1/bots/<uuid:bot_id>/sources/',v.SourceListView.as_view()),
    path('api/v1/bots/<uuid:bot_id>/sources/<uuid:source_id>/',v.SourceDetailView.as_view()),
    path('api/v1/bots/<uuid:bot_id>/chat/',v.ChatView.as_view()),
    path('api/v1/keys/',v.KeyListView.as_view()),
    path('api/v1/keys/<uuid:key_id>/revoke/',v.KeyRevokeView.as_view()),
    path('api/v1/keys/<uuid:key_id>/rotate/',v.KeyRotateView.as_view()),
    path('api/v1/conversations/',v.ConversationListView.as_view()),
    path('api/v1/conversations/<uuid:conversation_id>/',v.ConversationDetailView.as_view()),
    path('api/v1/usage/',v.UsageView.as_view()),
    path('api/v1/plans/',v.PlanListView.as_view()),
    path('api/v1/billing/checkout/',v.CheckoutView.as_view()),
    path('api/v1/billing/paystack/webhook/',v.WebhookView.as_view()),
    path('api/v1/health/live/',v.LiveView.as_view()),
    path('api/v1/health/ready/',v.ReadyView.as_view()),
    path('api/v1/widget/<uuid:bot_id>/session/',v.VisitorSessionView.as_view()),
    path('api/v1/widget/<uuid:bot_id>/chat/',v.WidgetChatView.as_view()),
    path('api/v1/schema/',SpectacularAPIView.as_view(),name='schema'),
    path('api/v1/docs/',SpectacularSwaggerView.as_view(url_name='schema'),name='swagger-ui'),
    path('widget/<uuid:bot_id>/',v.widget_frame),
    path('widget-example/',v.widget_example),
]
