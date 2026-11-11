from .settings import *
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
CELERY_TASK_ALWAYS_EAGER = True
CACHES = {'default': {'BACKEND':'django.core.cache.backends.locmem.LocMemCache'}}
REST_FRAMEWORK = {**REST_FRAMEWORK, 'DEFAULT_THROTTLE_CLASSES':[], 'DEFAULT_THROTTLE_RATES':{
    'general':'100000/min','login':'100000/min','registration':'100000/hour','chat':'100000/min','visitor':'100000/min'}}
MEDIA_ROOT = BASE_DIR / '.test-uploads'
