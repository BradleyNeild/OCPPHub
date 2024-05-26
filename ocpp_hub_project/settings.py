from pathlib import Path
import os
import environ

# Initialize environment variables
env = environ.Env()
environ.Env.read_env()

# Base directory of the project
BASE_DIR = Path(__file__).resolve().parent.parent

# Security settings
SECRET_KEY = env('SECRET_KEY', default='default_secret_key')
DEBUG = env.bool('DEBUG', default=True)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=['localhost', '127.0.0.1', '[::1]', 'web'])

# Application definition
INSTALLED_APPS = [
    # Django apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',  # Required by allauth

    # Third-party apps
    'rest_framework',
    'oauth2_provider',
    'corsheaders',
    'allauth',
    'allauth.account',

    # Your apps
    'ocpp_hub_app',
]

DEFAULT_USERS = [
    {
        'username': 'root',
        'email': 'bogglesby6@gmail.com',
        'password': '123',
        'is_superuser': True,
    },
]

# Allauth configuration
ACCOUNT_LOGOUT_ON_GET = True  # Ensure this is set if you want logout to happen via GET request
ACCOUNT_LOGOUT_ON_PASSWORD_CHANGE = True

# Middleware configuration
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',  # This should be before your custom middleware
    'allauth.account.middleware.AccountMiddleware',  # Ensure allauth middleware is included
    'ocpp_hub_app.middleware.EmailVerificationRequiredMiddleware',  # Custom middleware after auth middleware
    'oauth2_provider.middleware.OAuth2TokenMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'ocpp_hub_app.middleware.CustomExceptionMiddleware',
]

# CORS configuration
CORS_ORIGIN_ALLOW_ALL = True

# Site ID for allauth
SITE_ID = 1

# Email backend configuration
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = env('EMAIL_HOST_USER', default='default-email@gmail.com')
EMAIL_HOST_PASSWORD = env('EMAIL_HOST_PASSWORD', default='default-password')

if EMAIL_HOST_USER == 'default-email@gmail.com' or EMAIL_HOST_PASSWORD == 'default-password':
    print("Warning: EMAIL_HOST_USER and EMAIL_HOST_PASSWORD are set to default values. Email functionality will not work.")

# Authentication backends
AUTHENTICATION_BACKENDS = [
    'oauth2_provider.backends.OAuth2Backend',
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Allauth settings
ACCOUNT_AUTHENTICATION_METHOD = 'username'
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_EMAIL_VERIFICATION = 'mandatory'
ACCOUNT_USERNAME_REQUIRED = True
ACCOUNT_SIGNUP_PASSWORD_ENTER_TWICE = True
ACCOUNT_UNIQUE_EMAIL = False
LOGIN_REDIRECT_URL = '/dashboard/'

# Account rate limits
ACCOUNT_RATE_LIMITS = {
    'login.failed': '5/300s',  # 5 failed login attempts in 5 minutes
}

# OAuth2 settings
if DEBUG:
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

OAUTH2_CLIENT_ID = env('OAUTH2_CLIENT_ID', default='default-client-id')  # Replace with the actual client ID for OCPPHub
OAUTH2_CLIENT_SECRET = env('OAUTH2_CLIENT_SECRET', default='default-client-secret')  # Replace with the actual client secret for OCPPHub

# OAuth2 provider URLs
OAUTH2_AUTHORIZATION_URL = env('OAUTH2_AUTHORIZATION_URL', default='http://localhost:8002/o/authorize/')
OAUTH2_TOKEN_URL = env('OAUTH2_TOKEN_URL', default='http://localhost:8002/o/token/')

# Add the OAuth2 toolkit configurations
OAUTH2_PROVIDER = {
    'ACCESS_TOKEN_EXPIRE_SECONDS': 36000,
    'REFRESH_TOKEN_EXPIRE_SECONDS': 86400,
    'ALLOW_REFRESH': True,
    'AUTHORIZATION_CODE_EXPIRE_SECONDS': 600,
    'OAUTH2_VALIDATOR_CLASS': 'ocpp_hub_app.custom_oauth2_validator.CustomOAuth2Validator',
    'SCOPES': {
        'read': 'Read scope',
        'write': 'Write scope',
        'proxy_access': 'Access for proxy client',
        'chargepoints:read': 'Read chargepoints',
        'chargepoints:write': 'Write chargepoints',
        'ocpp:config': 'Edit OCPP configuration',
        'ocpp:meter': 'Request meter values',
    }
}

# Django REST Framework settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'oauth2_provider.contrib.rest_framework.OAuth2Authentication',
        'rest_framework.authentication.SessionAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
}

# URL configuration
ROOT_URLCONF = 'ocpp_hub_project.urls'

# Template settings
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'ocpp_hub_app/templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

# WSGI application
WSGI_APPLICATION = 'ocpp_hub_project.wsgi.application'

# Database configuration
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [os.path.join(BASE_DIR, 'ocpp_hub_app', 'static')]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
