"""
Local-only settings override for running `manage.py check`/`test` on a
laptop without Postgres or Docker. NEVER used in Docker/production — the
image's entrypoint always uses config.settings (Postgres).

Usage:
    DJANGO_SETTINGS_MODULE=config.settings_test_sqlite python manage.py test
"""
from .settings import *  # noqa: F401,F403

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',  # noqa: F405
    }
}

# The test client makes plain HTTP requests with no X-Forwarded-Proto
# header (there's no nginx in front of it here), so the production
# SECURE_SSL_REDIRECT would 301 every single test request. Real
# production config (config.settings) is untouched.
SECURE_SSL_REDIRECT = False

# CompressedManifestStaticFilesStorage requires collectstatic to have run
# (entrypoint.sh does this before gunicorn starts, in production). Test
# runs here bypass the entrypoint entirely, so there's no manifest —
# fall back to plain StaticFilesStorage, which needs no manifest and is
# all {% static %} tags need for tests to render.
STORAGES = {
    **STORAGES,  # noqa: F405
    'staticfiles': {
        'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
    },
}

# django-axes' AxesBackend requires a `request` argument to authenticate()
# (it needs one to know the caller's IP) — but Django's test-client
# shortcut `self.client.login(...)` calls authenticate() with no request
# at all, since it exists purely to establish a session for tests that
# aren't exercising the login view itself (e.g. logout tests). That
# shortcut is unrelated to axes and used throughout this suite, so axes
# is switched off for the generic test run to keep it working. This does
# NOT weaken real coverage: axes' actual lockout behavior is exercised
# directly by accounts.tests.test_auth.BruteForceLockoutTests, which
# re-enables it and posts real requests through the real login view, the
# same path production traffic takes. Production (config.settings) never
# imports this file and is unaffected either way.
AXES_ENABLED = False
