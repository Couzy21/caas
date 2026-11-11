from django.core.management.base import BaseCommand
from core.models import PaymentTransaction,WebhookEvent
from core.services.billing import reconcile
from core.api import Problem
class Command(BaseCommand):
    help = 'Safely verify pending Paystack purchases; no charges are initiated.'
    def add_arguments(self,parser):
        parser.add_argument('--reference')
    def handle(self,*args,**options):
        qs = PaymentTransaction.objects.filter(entitlement__isnull=True).order_by('created_at')
        if options['reference']:
            qs = qs.filter(reference=options['reference'])
        for payment in qs[:100]:
            try:
                reconcile(payment)
                WebhookEvent.objects.filter(payment=payment,event_type='charge.success').update(status='processed',error='')
                self.stdout.write(f'{payment.reference}: verified')
            except Problem:
                PaymentTransaction.objects.filter(pk=payment.pk).update(error='Verification unavailable or mismatched.')
                self.stdout.write(f'{payment.reference}: not verified; no access granted')
