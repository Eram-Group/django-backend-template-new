"""Local development settings: DEBUG, Mailpit email, toolbar, inline tasks."""

import socket

from config.env import env
from config.settings.base import *

DEBUG = True

# --- Email -> Mailpit ---------------------------------------------------------
# base.MAILERS already points the default mailer at SMTP with the env host/port
# (localhost:1025), so local dev needs no override here.

# --- Dev tooling ---------------------------------------------------------------
INSTALLED_APPS += [
    "debug_toolbar",
    "django_extensions",
]
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "debug_toolbar.middleware.DebugToolbarMiddleware",
)

# Toolbar visibility: localhost plus the Docker-internal gateway addresses
# (inside a container the browser's IP is the network gateway, not 127.0.0.1).
INTERNAL_IPS = ["127.0.0.1"]
try:
    _hostname, _aliases, _container_ips = socket.gethostbyname_ex(socket.gethostname())
    INTERNAL_IPS += [ip[: ip.rfind(".")] + ".1" for ip in _container_ips]
except OSError:  # pragma: no cover - depends on host network config
    pass

# The toolbar injects inline scripts; CSP stays a production concern.
SECURE_CSP = {}

# --- Tasks: inline by default, flip TASKS_IMMEDIATE=false to exercise db_worker
if env.TASKS_IMMEDIATE:
    TASKS = {"default": {"BACKEND": "django.tasks.backends.immediate.ImmediateBackend"}}
