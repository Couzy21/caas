from pathlib import Path
import environ
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')
DEMO_MODE = env.bool('DEMO_MODE', default=False)
DEBUG = env.bool('DEBUG', default=False)
SECRET_KEY = env('SECRET_KEY', default='local-demo-only-secret-change-me' if DEMO_MODE else '')
if not SECRET_KEY or (not DEMO_MODE and len(SECRET_KEY) < 50):
    raise ImproperlyConfigured('Set a random SECRET_KEY of at least 50 characters.')
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', 'testserver'] if DEMO_MODE else [])
INSTALLED_APPS = ['django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
                  'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
                  'rest_framework', 'rest_framework_simplejwt.token_blacklist', 'drf_spectacular', 'core']
MIDDLEWARE = ['django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
              'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware',
              'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware',
              'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware']
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'],
              'APP_DIRS': True, 'OPTIONS': {'context_processors': ['django.template.context_processors.request',
              'django.contrib.auth.context_processors.auth', 'django.contrib.messages.context_processors.messages']}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': env.db('DATABASE_URL', default=f'sqlite:///{BASE_DIR}/demo.sqlite3' if DEMO_MODE else 'postgres://caas:caas@db:5432/caas')}
if not DEMO_MODE and DATABASES['default']['ENGINE'] != 'django.db.backends.postgresql':
    raise ImproperlyConfigured('Production requires PostgreSQL with pgvector.')
AUTH_USER_MODEL = 'core.User'
AUTH_PASSWORD_VALIDATORS = [{'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
                            {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
                            {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
MEDIA_ROOT = BASE_DIR / 'private_uploads'  # Never mapped to an HTTP route.
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
REDIS_URL = env('REDIS_URL', default='redis://redis:6379/0')
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'} if DEMO_MODE else
          {'BACKEND': 'django.core.cache.backends.redis.RedisCache', 'LOCATION': REDIS_URL}}
CELERY_BROKER_URL = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = env.bool('CELERY_TASK_ALWAYS_EAGER', default=False)
CELERY_TASK_TIME_LIMIT = 120
CELERY_TASK_SOFT_TIME_LIMIT = 110
CELERY_TASK_ACKS_LATE = True
CELERY_WORKER_PREFETCH_MULTIPLIER = 1
CELERY_BEAT_SCHEDULE = {'recover': {'task': 'core.tasks.recover', 'schedule': 60.0}}
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['core.auth.APIKeyAuthentication', 'rest_framework_simplejwt.authentication.JWTAuthentication'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.IsAuthenticated'],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'core.api.exception_handler',
    'DEFAULT_THROTTLE_CLASSES': ['core.auth.GeneralThrottle'],
    'DEFAULT_THROTTLE_RATES': {'general': '120/min', 'login': '10/min', 'registration': '5/hour', 'chat': '30/min', 'visitor': '10/min'},
}
SPECTACULAR_SETTINGS = {'TITLE': 'CAAS API', 'DESCRIPTION': 'Tenant-isolated business chat. Paid plans are manual-renewal 30-day purchases.',
                        'VERSION': '1.0.0', 'SERVE_INCLUDE_SCHEMA': False, 'COMPONENT_SPLIT_REQUEST': True}
from datetime import timedelta
SIMPLE_JWT = {'ACCESS_TOKEN_LIFETIME': timedelta(minutes=15), 'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
              'ROTATE_REFRESH_TOKENS': True, 'BLACKLIST_AFTER_ROTATION': True}
OPENAI_API_KEY = env('OPENAI_API_KEY', default='')
CHAT_MODEL = env('CHAT_MODEL', default='')
EMBEDDING_MODEL = env('EMBEDDING_MODEL', default='')
EMBEDDING_DIMENSIONS = 1536
PAYSTACK_SECRET_KEY = env('PAYSTACK_SECRET_KEY', default='')
PAYSTACK_WEBHOOK_SECRET = env('PAYSTACK_WEBHOOK_SECRET', default='')
PAYSTACK_ALLOW_LEGACY_SIGNATURE = env.bool('PAYSTACK_ALLOW_LEGACY_SIGNATURE', default=False)
PUBLIC_BASE_URL = env('PUBLIC_BASE_URL', default='http://localhost:8000')
if not DEMO_MODE and not all([OPENAI_API_KEY, CHAT_MODEL, EMBEDDING_MODEL]):
    raise ImproperlyConfigured('Production requires OPENAI_API_KEY, CHAT_MODEL and EMBEDDING_MODEL.')
# Conservative USD micro-dollar budgets, independent of NGN selling prices. Configure bounds for your model.
REQUEST_COST_MICROUSD = env.int('REQUEST_COST_MICROUSD', default=100000)
INPUT_MICROUSD_PER_TOKEN = env.int('INPUT_MICROUSD_PER_TOKEN', default=1)
OUTPUT_MICROUSD_PER_TOKEN = env.int('OUTPUT_MICROUSD_PER_TOKEN', default=5)
EMBED_MICROUSD_PER_TOKEN = env.int('EMBED_MICROUSD_PER_TOKEN', default=1)
MAX_MESSAGE_CHARS = 2000
MAX_CONTEXT_CHARS = 6000
MAX_OUTPUT_TOKENS = 400
MAX_SOURCE_CHARS = 100000
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_ORG_STORAGE_BYTES = env.int('MAX_ORG_STORAGE_BYTES', default=20 * 1024 * 1024)
MAX_CHUNKS = 150
RESERVATION_SECONDS = 180
SECURE_SSL_REDIRECT = not DEMO_MODE
SESSION_COOKIE_SECURE = not DEMO_MODE
CSRF_COOKIE_SECURE = not DEMO_MODE
SECURE_HSTS_SECONDS = 31536000 if not DEMO_MODE else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEMO_MODE
SECURE_HSTS_PRELOAD = not DEMO_MODE
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
LOGGING = {'version': 1, 'disable_existing_loggers': False, 'formatters': {'json': {'()': 'core.logging.SafeFormatter'}},
           'handlers': {'console': {'class': 'logging.StreamHandler', 'formatter': 'json'}},
           'root': {'handlers': ['console'], 'level': 'INFO'},
           'loggers': {'django.request': {'handlers': ['console'], 'level': 'ERROR', 'propagate': False}}}
