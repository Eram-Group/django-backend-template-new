"""Test settings: fast, isolated, everything in memory.

Also the settings django-stubs boots for mypy (pyproject django_settings_module).
Run with --no-migrations (pytest-django) when migration history slows suites.
"""

from config.settings.base import *

DEBUG = False

# Fast hashers - password strength is irrelevant in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# In-memory email and cache: createcachetable never runs in test databases,
# so the DatabaseCache default would fail on first hit.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-default",
    },
    "ratelimit": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-ratelimit",
    },
}

# Tasks run inline so assertions see their effects immediately.
TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}

# Lockout middleware would flake repeated-login tests.
AXES_ENABLED = False
