from django.core.management.base import BaseCommand
from core.models import Plan

class Command(BaseCommand):
    help = 'Create editable provisional plans. Existing plans are never overwritten.'
    def handle(self,*args,**options):
        for slug,name,price,bots,answers,budget in [('trial','Trial',0,1,50,3000000),
                ('starter','Starter',1500000,1,500,15000000),('business','Business',3000000,3,1500,30000000)]:
            Plan.objects.get_or_create(slug=slug,defaults={'name':name,'price_kobo':price,'bot_limit':bots,
                'answer_limit':answers,'spend_limit_microusd':budget})
        self.stdout.write('Plans seeded. Prices are assumptions; edit in admin. Access purchases last 30 days; manual renewal.')
