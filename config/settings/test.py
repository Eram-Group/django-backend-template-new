"""Minimal stub so mypy's django-stubs plugin can boot Django (G01/G02).

Rewritten from scratch in G03 (settings-base / settings-envs).
"""

SECRET_KEY = "insecure-g01-toolcfg-stub"  # noqa: S105
USE_TZ = True
INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "apps.common",
    "apps.users",
]
AUTH_USER_MODEL = "users.User"
