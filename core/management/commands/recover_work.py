from django.core.management.base import BaseCommand
from core.tasks import recover
class Command(BaseCommand):
    help = 'Recover stale usage reservations and republish queued work. Also run by Celery beat.'
    def handle(self,*args,**options):
        recover()
        self.stdout.write('Recovery completed.')
