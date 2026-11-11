from rest_framework import serializers
from drf_spectacular.utils import inline_serializer

from functools import lru_cache

@lru_cache(maxsize=None)
def envelope(serializer, many=False):
    name = serializer.__name__.replace('Serializer','')
    if many:
        return inline_serializer(name=name+'Page', fields={'message':serializers.CharField(), 'count':serializers.IntegerField(),
            'next':serializers.URLField(allow_null=True),'previous':serializers.URLField(allow_null=True),'results':serializer(many=True)})
    return inline_serializer(name=name+'Envelope',fields={'message':serializers.CharField(),'data':serializer(allow_null=True)})
