from django.db import transaction
from django.db.models.signals import post_delete
from django.dispatch import receiver
from core.models import KnowledgeSource

@receiver(post_delete,sender=KnowledgeSource)
def delete_private_file(sender,instance,**kwargs):
    if instance.file:
        storage,name = instance.file.storage,instance.file.name
        transaction.on_commit(lambda:storage.delete(name))
