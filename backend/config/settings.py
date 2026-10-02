[Reading 116 lines from start (total: 116 lines, 0 remaining)]

"""Production defaults; local testing requires explicit DJANGO_DEBUG=1."""
import os
from datetime import timedelta
from pathlib import Path
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
DEPLOYMENT_STATUS_FILE = Path(os.environ.get(
    'TRUD_DEPLOYMENT_STATUS_FILE', '/var/lib/trud-1/deployment-status.json',
))
DEBUG = os.environ.get('DJANGO_DEBUG') == '1'
SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', '')
if not SECRET_KEY:
    if not DEBUG:
        raise ImproperlyConfigured('Set DJANGO_SECRET_KEY before starting.')
    SECRET_KEY = 'local-development-only-do-not-use-on-a-server'
ALLOWED_HOSTS = os.environ.get('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1').split(',')
CSRF_TRUSTED_ORIGINS = list(filter(None, os.environ.get('DJANGO_CSRF_ORIGINS', '').split(',')))
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles',
    'axes', 'simple_history',
    'django_otp', 'django_otp.plugins.otp_static', 'django_otp.plugins.otp_totp',
    'two_factor', 'water', 'public_site',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', 'django_otp.middleware.OTPMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'simple_history.middleware.HistoryRequestMiddleware', 'axes.middleware.AxesMiddleware',
]
ROOT_URLCONF = 'config.urls'
WSGI_APPLICATION = 'config.wsgi.application'
TEMPLATES = [{
    'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [BASE_DIR / 'templates'], 'APP_DIRS': True,
    'OPTIONS': {'context_processors': [
        'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth',
        'django.contrib.messages.context_processors.messages',
        'water.security_context.security_alerts',
    ]},
}]
if DEBUG and os.environ.get('DJANGO_TEST_SQLITE') == '1':
    DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': BASE_DIR / 'local.sqlite3'}}
else:
    DATABASES = {'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.environ.get('PGDATABASE', 'trud_site'),
        'USER': os.environ.get('PGUSER', 'trud_site'),
        'PASSWORD': os.environ.get('PGPASSWORD', ''),
        'HOST': os.environ.get('PGHOST', '127.0.0.1'),
        'PORT': os.environ.get('PGPORT', '5432'),
        'CONN_MAX_AGE': 60,
    }}
AUTH_USER_MODEL = 'water.User'
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', 'OPTIONS': {'min_length': 6}},
]
AUTHENTICATION_BACKENDS = ['axes.backends.AxesStandaloneBackend', 'django.contrib.auth.backends.ModelBackend']
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(minutes=15)
AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']
AXES_CLIENT_IP_CALLABLE = 'config.network.client_ip'
AXES_RESET_ON_SUCCESS = True
AXES_ENABLE_ACCESS_FAILURE_LOG = False
SIMPLE_HISTORY_REVERT_DISABLED = True
SIMPLE_HISTORY_ENFORCE_HISTORY_MODEL_PERMISSIONS = True
LOGIN_URL = 'two_factor:login'
LOGIN_REDIRECT_URL = '/admin/'
LOGOUT_REDIRECT_URL = 'two_factor:login'
TWO_FACTOR_LOGIN_TIMEOUT = 600
TWO_FACTOR_REMEMBER_COOKIE_AGE = None
OTP_TOTP_ISSUER = 'ТСН ТРУД-1'
OTP_ADMIN_HIDE_SENSITIVE_DATA = True
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Europe/Moscow'
USE_I18N = True
USE_TZ = True
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
STATIC_URL = '/admin-static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'config.storage.PublicStaticFilesStorage'},
}
MEDIA_ROOT = BASE_DIR.parent / 'private-data'
FILE_UPLOAD_PERMISSIONS = 0o600
FILE_UPLOAD_DIRECTORY_PERMISSIONS = 0o700
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SAMESITE = 'Lax'
CSRF_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = 8 * 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_SSL_REDIRECT = not DEBUG
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'
SECURE_CROSS_ORIGIN_OPENER_POLICY = 'same-origin'
X_FRAME_OPTIONS = 'DENY'
if os.environ.get('DJANGO_TRUST_PROXY') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Appeal attachment security. Production fails closed if malware scanning is unavailable.
APPEAL_MALWARE_SCAN_REQUIRED = os.environ.get(
    'TRUD_APPEAL_MALWARE_SCAN_REQUIRED', '0' if DEBUG else '1',
) == '1'
APPEAL_CLAMDSCAN_PATH = os.environ.get('TRUD_APPEAL_CLAMDSCAN_PATH', '/usr/bin/clamdscan')
APPEAL_CLAMDSCAN_TIMEOUT = int(os.environ.get('TRUD_APPEAL_CLAMDSCAN_TIMEOUT', '20'))
APPEAL_POST_ATTEMPTS_PER_MINUTE = int(os.environ.get('TRUD_APPEAL_POST_ATTEMPTS_PER_MINUTE', '12'))
APPEAL_MESSAGES_PER_MINUTE = int(os.environ.get('TRUD_APPEAL_MESSAGES_PER_MINUTE', '5'))
APPEAL_NEW_PER_TEN_MINUTES = int(os.environ.get('TRUD_APPEAL_NEW_PER_TEN_MINUTES', '3'))
APPEAL_DAILY_UPLOAD_BYTES = int(os.environ.get('TRUD_APPEAL_DAILY_UPLOAD_BYTES', str(50 * 1024 * 1024)))
APPEAL_TOTAL_UPLOAD_BYTES = int(os.environ.get('TRUD_APPEAL_TOTAL_UPLOAD_BYTES', str(100 * 1024 * 1024)))
