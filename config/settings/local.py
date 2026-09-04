"""Local development settings: DEBUG, Mailpit email, debug toolbar.

Tasks go through the real DB queue exactly as deployed - run `just worker`
next to `just run`.
"""

from config.settings.base import *

DEBUG = True

# --- Dev tooling ---------------------------------------------------------------
INSTALLED_APPS += [
    "debug_toolbar",
    "django_extensions",
]
MIDDLEWARE.insert(
    MIDDLEWARE.index("django.middleware.security.SecurityMiddleware") + 1,
    "debug_toolbar.middleware.DebugToolbarMiddleware",
)
INTERNAL_IPS = ["127.0.0.1"]
# SECURE_CSP stays as deployed: the toolbar nonces its scripts from
# request.csp_nonce, so the admin is developed under the real policy.
