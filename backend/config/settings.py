"""
Django settings for the Recreo Bienestar backend/admin.

All environment-specific values come from environment variables (see
.env.example). Nothing secret is hardcoded here, and secure defaults are
used unless explicitly overridden.
"""
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    DJANGO_SECURE_SSL_REDIRECT=(bool, True),
)
# In containers, real values come from the environment (env_file in
# docker-compose). A local .env is only used for local development and is
# never committed.
environ.Env.read_env(BASE_DIR / '.env')

# ── Core ─────────────────────────────────────────────────────────────────
SECRET_KEY = env('SECRET_KEY')
DEBUG = env('DEBUG')

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[])

# Custom admin mount point, e.g. "gestion/". Kept configurable so the path
# can change without a code deploy. Falls back to the proposed default.
ADMIN_URL = env('ADMIN_URL', default='gestion/')

# ── Applications ─────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_filters',
    'axes',
    'accounts',
    'catalog',
    'memberships',
    'site_content',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    # Last, per django-axes' own requirement — needs to see the response
    # AuthenticationMiddleware/the view produced before deciding whether to
    # count/lock out this attempt.
    'axes.middleware.AxesMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        # Project-level templates (base.html shared by every app) live in
        # backend/templates/; each app's own templates are still found via
        # APP_DIRS below.
        'DIRS': [BASE_DIR / 'templates'],
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

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# ── Database ─────────────────────────────────────────────────────────────
# Dedicated Postgres container, reachable only on the internal Docker
# network (see docker-compose). Never exposed publicly.
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': env('DB_NAME'),
        'USER': env('DB_USER'),
        'PASSWORD': env('DB_PASSWORD'),
        'HOST': env('DB_HOST'),
        'PORT': env('DB_PORT', default='5432'),
    }
}

# ── Password validation ──────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ── Internationalization ─────────────────────────────────────────────────
LANGUAGE_CODE = 'es-ar'
TIME_ZONE = 'America/Argentina/Buenos_Aires'
USE_I18N = True
USE_TZ = True

# ── Static & media ───────────────────────────────────────────────────────
# Served directly by WhiteNoise from within the app container. Top-level
# prefixes now that this container serves public member-facing pages too,
# not just Django Admin (Phase 1 nested these under /gestion/ — moved back
# out to /static/ and /media/ here; nginx was never reloaded with the old
# paths live, see deploy/PHASE2_DELIVERABLES.md, so nothing public depended
# on the old prefix).
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
# Bug fix (2026-08-10): this dict previously had only 'staticfiles'.
# Django's STORAGES setting is NOT merged with its own built-in default —
# declaring it at all replaces the whole thing, so omitting 'default' left
# nothing registered under that alias. Any ImageField/FileField save
# (Profile.avatar was the first ever exercised in production) lazily
# resolves the default backend via storages['default'] and raised
# InvalidStorageError: Could not find config for 'default' in
# settings.STORAGES — a real production 500 on every profile photo
# upload, not caught by the test suite because no test ever POSTed an
# actual file to ProfileForm. 'default' explicitly set to Django's own
# ordinary FileSystemStorage default (this project has no S3/CDN for
# media yet) fixes it without masking anything.
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Authentication ───────────────────────────────────────────────────────
# AxesBackend MUST be first — it's a gate, not a credential checker: it
# short-circuits authentication entirely (regardless of which backend
# below would have accepted the credentials) once an account/IP is locked
# out. Covers BOTH /ingresar/ (accounts.views.MemberLoginView) and
# /gestion/login/ (Django Admin's own built-in login) uniformly, since
# both ultimately call django.contrib.auth.authenticate() — no per-view
# wiring needed. EmailOrUsernameModelBackend next (lets members log in
# with either); ModelBackend stays as a fallback so anything relying on
# default username-only behavior is unaffected.
AUTHENTICATION_BACKENDS = [
    'axes.backends.AxesBackend',
    'accounts.backends.EmailOrUsernameModelBackend',
    'django.contrib.auth.backends.ModelBackend',
]

LOGIN_URL = '/ingresar/'
LOGIN_REDIRECT_URL = '/mi-cuenta/'
LOGOUT_REDIRECT_URL = '/'

# ── Brute-force protection (SECURITY_AUDIT.md HIGH-1) ────────────────────
# 5 failed attempts (per username+IP combination) locks that combination
# out for 1 hour. A successful login resets the counter. Deliberately
# scoped to username+IP (the django-axes default), not IP alone — a
# shared IP (office, campus, carrier-grade NAT) failing to log into one
# account shouldn't lock every account behind that IP out of everything.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1  # hours
AXES_LOCKOUT_PARAMETERS = ['username', 'ip_address']
AXES_RESET_COOL_OFF_ON_FAILURE_DURING_LOCKOUT = True
# Plain-text, no template dependency — avoids needing a dedicated lockout
# page for what's meant to be a rare, self-explanatory event.
#
# NOTE: the setting axes actually reads here is AXES_COOLOFF_MESSAGE, not
# AXES_LOCKOUT_MESSAGE (that name doesn't exist in django-axes and is
# silently ignored — get_lockout_message() picks AXES_COOLOFF_MESSAGE
# whenever AXES_COOLOFF_TIME is set, which it is above, and only falls
# back to AXES_PERMALOCK_MESSAGE for permanent lockouts). Caught during
# Stage A validation by inspecting axes.helpers.get_lockout_message
# directly rather than assuming the setting name.
AXES_COOLOFF_MESSAGE = (
    'Demasiados intentos fallidos. Probá de nuevo en un rato, '
    'o usá "¿Olvidaste tu contraseña?" para recuperar el acceso.'
)

# ── Email ────────────────────────────────────────────────────────────────
# Console backend only — password reset emails are printed to the
# recreo-django container logs (`docker logs recreo-django`), not actually
# sent. This is intentional for this phase (no real transactional email is
# configured yet). Production will need: EMAIL_BACKEND switched to SMTP,
# EMAIL_HOST/PORT/HOST_USER/HOST_PASSWORD/USE_TLS, and DEFAULT_FROM_EMAIL —
# most likely via SES given the AWS-hosted stack, added as env vars the
# same way DB_* are handled, never hardcoded here.
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
DEFAULT_FROM_EMAIL = 'Recreo Bienestar <no-reply@recreobienestar.com>'

# ── REST API ─────────────────────────────────────────────────────────────
# Read-only, public, same-origin (served under /api/ on the same domain as
# the static site — no separate frontend origin exists yet, so no CORS
# package is installed; adding one later should default to an explicit
# allow-list, never a wildcard).
#
# AllowAny is still correct: nothing under /api/ requires being logged in
# to use. But — unlike when this comment first said "there is no gated
# content this API can accidentally leak" (true in Phase 2, before
# per-video membership access levels existed as a public concept) —
# catalog.views.VideoViewSet now DOES check can_access_video() per request,
# which needs to know the real caller, not always AnonymousUser. Session
# authentication (safe for GET; DRF only enforces its CSRF check on unsafe
# methods, and there are none here) is what lets that resolve correctly
# for a logged-in same-origin browser session.
REST_FRAMEWORK = {
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': ['django_filters.rest_framework.DjangoFilterBackend'],
    'DEFAULT_PERMISSION_CLASSES': ['rest_framework.permissions.AllowAny'],
    'DEFAULT_AUTHENTICATION_CLASSES': ['rest_framework.authentication.SessionAuthentication'],
    'DEFAULT_RENDERER_CLASSES': ['rest_framework.renderers.JSONRenderer'],
}
if DEBUG:
    REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'].append('rest_framework.renderers.BrowsableAPIRenderer')

# ── Security ──────────────────────────────────────────────────────────────
# This service always sits behind the nginx reverse proxy, which terminates
# TLS and forwards the original scheme.
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
USE_X_FORWARDED_HOST = True

SECURE_SSL_REDIRECT = env('DJANGO_SECURE_SSL_REDIRECT') and not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
# True: nothing in this app reads the CSRF cookie via JS — every form
# (login, register, profile, password reset, logout) submits the token
# through the standard hidden {% csrf_token %} input, not an AJAX header.
# If a future feature needs the token in JS (e.g. a fetch() call), prefer
# reading it from a {% csrf_token %} rendered into a <meta> tag over
# flipping this back — keeps the cookie itself inaccessible to any script,
# including an XSS payload, either way.
CSRF_COOKIE_HTTPONLY = True

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

if not DEBUG:
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
