"""Test settings: fast, isolated, everything in memory.

Also the settings django-stubs boots for mypy (pyproject django_settings_module).
"""

from config.settings.base import *

DEBUG = False

# Fast hashers - password strength is irrelevant in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# In-memory email and cache: createcachetable never runs in test databases,
# so the DatabaseCache default would fail on first hit.
MAILERS = {"default": {"BACKEND": "django.core.mail.backends.locmem.EmailBackend"}}

# SMS/push/WhatsApp transports are swapped for in-memory outboxes by the
# ``outboxes`` autouse fixture in apps/notifications/tests/conftest.py - tests
# never touch provider HTTP even when real creds sit in a developer's .env.

# Every currency resolves to the test FakeGateway, so suites never hit
# Tap/Paymob even with test keys in .env.
PAYMENT_GATEWAYS = {
    "SAR": "apps.payments.tests.fake_gateway.FakeGateway",
    "EGP": "apps.payments.tests.fake_gateway.FakeGateway",
}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "test-default",
    },
}

# Tasks run inline so assertions see their effects immediately.
TASKS = {
    "default": {
        "BACKEND": "django.tasks.backends.immediate.ImmediateBackend",
        "QUEUES": ["default", "bulk"],
    }
}

# Lockout middleware would flake repeated-login tests.
AXES_ENABLED = False
