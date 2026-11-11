import secrets
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from core.models import User,Organisation,Membership,Plan,Bot
from core.services.usage import grant
from core.services.knowledge import save_source

class Command(BaseCommand):
    help = 'Seed an explicitly local demo; never allowed in production.'
    @transaction.atomic
    def handle(self,*args,**options):
        if not settings.DEMO_MODE:
            raise CommandError('seed_demo requires DEMO_MODE=true.')
        call_command('seed_plans')
        if User.objects.filter(username='demo').exists():
            self.stdout.write('Demo already exists. Use the original password or manage.py changepassword demo.')
            return
        password = secrets.token_urlsafe(18)
        user = User.objects.create_user(username='demo',email='demo@example.test',password=password)
        org = Organisation.objects.create(name='Lagos Demo Store')
        Membership.objects.create(user=user,organisation=org,role='owner')
        grant(org,Plan.objects.get(pk='trial'))
        bot = Bot.objects.create(organisation=org,name='Lagos Store Assistant',allowed_origins=['http://localhost:8000','http://127.0.0.1:8000'])
        save_source(org,bot,{'title':'Delivery FAQ','kind':'faq','text':'What is the delivery fee? Delivery within Lagos costs ₦2,500. Orders arrive in 2 to 3 business days. We accept returns within 7 days.'})
        self.stdout.write(f'LOCAL DEMO ONLY. No AI or real embeddings. Username: demo  Password: {password}\nBot: {bot.pk}\nWidget example: {settings.PUBLIC_BASE_URL}/widget-example/?bot={bot.pk}')
