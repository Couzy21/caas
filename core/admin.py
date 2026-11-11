from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from core import models as m

admin.site.register(m.User, UserAdmin)
for model in [m.Organisation,m.Membership,m.Bot,m.Plan]:
    admin.site.register(model)

class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self,request):
        return False
    def has_change_permission(self,request,obj=None):
        return False
    def has_delete_permission(self,request,obj=None):
        return False

@admin.register(m.KnowledgeSource)
class SourceAdmin(ReadOnlyAdmin):
    list_display = ['title','organisation','bot','status','error','created_at']
    list_filter = ['status']

@admin.register(m.APIKey)
class KeyAdmin(ReadOnlyAdmin):
    exclude = ['secret_hash']
    list_display = ['name','prefix','organisation','revoked_at','expires_at']

for model in [m.Entitlement,m.UsagePeriod,m.UsageRecord,m.ProviderAttempt,m.PaymentTransaction,m.WebhookEvent,
              m.DocumentChunk,m.Conversation,m.Message,m.VisitorSession]:
    admin.site.register(model,ReadOnlyAdmin)
