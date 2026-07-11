"""Production/staging settings: hardened, S3, SES, Sentry, JSON logs.

The local compose stack runs THIS module with ENVIRONMENT=local (the image
carries no dev deps, so local.py is impossible in containers): deployment-only
behaviors gate on _DEPLOYED so prod code paths run over plain http + Mailpit.

Consumes env.AWS_* / SENTRY_DSN directly: a deployed container missing
them fails at boot or at the release step - there is no separate
required-in-prod validator by design.
"""

import sentry_sdk
import structlog

from config.env import env
from config.settings.base import *

_DEPLOYED = env.ENVIRONMENT != "local"

# --- Security hardening (check --deploy must report zero warnings, G12) --------
SECURE_SSL_REDIRECT = _DEPLOYED
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")  # behind the ALB
SECURE_HSTS_SECONDS = 31536000  # 1 year (preload-eligible)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = _DEPLOYED
CSRF_COOKIE_SECURE = _DEPLOYED
if _DEPLOYED:
    # __Secure- prefixed names require the Secure flag (break plain http).
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
if _DEPLOYED:
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

# --- Email via SES (Anymail); compose containers keep SMTP -> Mailpit ------------
if _DEPLOYED:
    EMAIL_BACKEND = "anymail.backends.amazon_ses.EmailBackend"
    ANYMAIL = {
        "AMAZON_SES_CLIENT_PARAMS": {"region_name": env.AWS_SES_REGION},
    }

# --- Sentry ------------------------------------------------------------------------
if env.SENTRY_DSN:
    sentry_sdk.init(
        dsn=env.SENTRY_DSN,
        environment=env.ENVIRONMENT,
        traces_sample_rate=env.SENTRY_TRACES_SAMPLE_RATE,
        send_default_pii=False,
    )

# --- Logging: JSON to stdout; host-poking noise silenced -----------------------------
LOGGING["formatters"]["structlog"]["processor"] = structlog.processors.JSONRenderer()
LOGGING["loggers"] = {
    "django.security.DisallowedHost": {"handlers": [], "propagate": False},
}
