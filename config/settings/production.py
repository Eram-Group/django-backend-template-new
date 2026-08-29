"""Deployed settings (dev / staging / production): hardened, S3, SES, Sentry,
JSON logs. Every value here is unconditional - a deployed environment runs
exactly one way. Local development uses ``local.py``; the image is only ever
booted with this module.

Consumes env.AWS_* / SENTRY_* directly; ``config.env`` refuses to load a
deployed environment without them.
"""

import sentry_sdk
import structlog

from config.env import env
from config.settings.base import *

# --- Security hardening (check --deploy must report zero warnings) ------------
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # behind the ALB
SECURE_HSTS_SECONDS = 31536000  # 1 year (preload-eligible)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
# __Secure- prefixed names require the Secure flag.
SESSION_COOKIE_NAME = "__Secure-sessionid"
CSRF_COOKIE_NAME = "__Secure-csrftoken"

# --- Database: psycopg native pool owns connection health ----------------------
# CONN_MAX_AGE=0 is the required pairing; no CONN_HEALTH_CHECKS (that is for
# persistent-connection setups).
DATABASES["default"]["CONN_MAX_AGE"] = 0
DATABASES["default"]["OPTIONS"] = {
    "pool": {
        "min_size": env.DB_POOL_MIN_SIZE,
        "max_size": env.DB_POOL_MAX_SIZE,
        "timeout": env.DB_POOL_TIMEOUT,
        "max_lifetime": env.DB_POOL_MAX_LIFETIME,
        "max_idle": env.DB_POOL_MAX_IDLE,
    },
}

# --- Static/media on S3 (collectstatic runs in the release step, never at build)
STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env.AWS_STORAGE_BUCKET_NAME,
            "region_name": env.AWS_S3_REGION_NAME,
            "custom_domain": env.AWS_S3_CUSTOM_DOMAIN,
            "location": "media",
            "file_overwrite": False,
        },
    },
    "staticfiles": {
        "BACKEND": "storages.backends.s3.S3ManifestStaticStorage",
        "OPTIONS": {
            "bucket_name": env.AWS_STORAGE_BUCKET_NAME,
            "region_name": env.AWS_S3_REGION_NAME,
            "custom_domain": env.AWS_S3_CUSTOM_DOMAIN,
            "location": "static",
        },
    },
}

# CloudFront is a separate origin: every fetch directive must allow it or the
# admin loads bare.
SECURE_CSP = {
    directive: [*sources, f"https://{env.AWS_S3_CUSTOM_DOMAIN}"]
    for directive, sources in SECURE_CSP.items()
}

# --- Email via SES (Anymail) -----------------------------------------------------
MAILERS = {"default": {"BACKEND": "anymail.backends.amazon_ses.EmailBackend"}}
ANYMAIL = {
    "AMAZON_SES_CLIENT_PARAMS": {"region_name": env.AWS_SES_REGION},
}

# --- Sentry ------------------------------------------------------------------------
sentry_sdk.init(
    dsn=env.SENTRY_DSN,
    environment=env.ENVIRONMENT,
    release=env.SENTRY_RELEASE,
    traces_sample_rate=env.SENTRY_TRACES_SAMPLE_RATE,
    send_default_pii=False,
)

# --- Logging: JSON to stdout; host-poking noise silenced -----------------------------
LOGGING["formatters"]["structlog"]["processor"] = structlog.processors.JSONRenderer()
LOGGING["loggers"] = {
    "django.security.DisallowedHost": {"handlers": [], "propagate": False},
}
