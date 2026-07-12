"""Base settings shared by local/production/test.

Every environment-dependent value comes from the typed ``config.env.env``
singleton - never from ``os.environ`` directly.
"""

from datetime import timedelta
from pathlib import Path
from typing import Any

import dj_database_url
import structlog
from django.urls import reverse_lazy
from django.utils.csp import CSP
from django.utils.translation import gettext_lazy as _
from import_export.formats import base_formats

from config.env import env

BASE_DIR = Path(__file__).resolve(strict=True).parent.parent.parent

# --- Core ---------------------------------------------------------------
SECRET_KEY = env.SECRET_KEY.get_secret_value()
SECRET_KEY_FALLBACKS = [key.get_secret_value() for key in env.SECRET_KEY_FALLBACKS]
DEBUG = False  # local.py turns it on
ALLOWED_HOSTS = env.ALLOWED_HOSTS
ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# --- Apps ---------------------------------------------------------------
INSTALLED_APPS = [
    # These MUST precede django.contrib.admin (load order is load-bearing).
    "modeltranslation",
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.import_export",
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "ninja",  # registers export_openapi_schema (the Apidog sync artifact)
    "corsheaders",
    "allauth",
    "allauth.account",
    "allauth.headless",
    "allauth.usersessions",
    "import_export",
    "django_tasks_db",
    "django_structlog",
    "django_guid",
    "axes",
    # Local
    "apps.common",
    "apps.users",
]

# --- Middleware (guid first, axes last - order is load-bearing) ----------
MIDDLEWARE = [
    "django_guid.middleware.guid_middleware",
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.csp.ContentSecurityPolicyMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_structlog.middlewares.RequestMiddleware",
    "axes.middleware.AxesMiddleware",
]

# --- Templates (admin + allauth need request/auth/messages) --------------
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# --- Database (pool OPTIONS + CONN_MAX_AGE=0 are production-only) ---------
DATABASES = {
    "default": {
        **dj_database_url.parse(env.DATABASE_URL),
        "ATOMIC_REQUESTS": True,
    },
}

# --- Cache / sessions / tasks (Postgres-only infrastructure) --------------
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",  # created by `manage.py createcachetable`
    },
    # Per-container counters for django-ratelimit (PLAN: upgrade = Redis/WAF).
    "ratelimit": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "ratelimit",
    },
}
RATELIMIT_USE_CACHE = "ratelimit"
SESSION_ENGINE = "django.contrib.sessions.backends.db"
TASKS = {"default": {"BACKEND": "django_tasks_db.DatabaseBackend"}}

# --- Auth -----------------------------------------------------------------
AUTH_USER_MODEL = "users.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",  # must come first
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.BCryptSHA256PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": f"django.contrib.auth.password_validation.{validator}"}
    for validator in (
        "UserAttributeSimilarityValidator",
        "MinimumLengthValidator",
        "CommonPasswordValidator",
        "NumericPasswordValidator",
    )
]

# django-axes: lock the (username, ip) combination after repeated failures.
AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = timedelta(hours=1)
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]

# --- Passwordless headless auth (allauth) ----------------------------------
ACCOUNT_ADAPTER = "apps.users.adapters.AccountAdapter"
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_LOGIN_METHODS = {"email"}
ACCOUNT_SIGNUP_FIELDS = ["email*"]  # no password anywhere in signup
ACCOUNT_LOGIN_BY_CODE_ENABLED = True
# Method-list semantics: these methods (and unknown ones) require the email
# OTP; "code" is always exempt and social logins already prove identity.
ACCOUNT_LOGIN_BY_CODE_REQUIRED = ["password"]
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_VERIFICATION_BY_CODE_ENABLED = True

# API-only: no server-rendered account pages; browser + app clients are the
# HEADLESS_CLIENTS default. The auth OpenAPI spec serves at /_allauth/openapi.json.
HEADLESS_ONLY = True
HEADLESS_SERVE_SPECIFICATION = True
HEADLESS_FRONTEND_URLS = {
    "account_confirm_email": f"{env.FRONTEND_BASE_URL}/auth/verify-email/{{key}}",
    "account_reset_password": f"{env.FRONTEND_BASE_URL}/auth/password-reset",
    "account_reset_password_from_key": (
        f"{env.FRONTEND_BASE_URL}/auth/password-reset/{{key}}"
    ),
    "account_signup": f"{env.FRONTEND_BASE_URL}/auth/signup",
}

# --- i18n / tz (site default: Arabic - user decision 2026-07-11) -----------
LANGUAGE_CODE = "ar"
LANGUAGES = [
    ("ar", _("Arabic")),
    ("en", _("English")),
]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]
MODELTRANSLATION_DEFAULT_LANGUAGE = "ar"
MODELTRANSLATION_FALLBACK_LANGUAGES = ("ar", "en")

# --- Admin (unfold) ----------------------------------------------------------
# SECURE_ADMIN_LOGIN env flag (off by default) swaps the admin's password
# login for allauth's email-code flow - wired in config/urls.py.


def environment_callback(request: Any) -> list[str]:
    """Colored header badge - the wrong-environment guard. The Literal env
    field keeps this mapping exhaustive."""
    return {
        "production": ["PRODUCTION", "danger"],
        "dev": ["DEV", "warning"],
        "local": ["LOCAL", "success"],
    }[env.ENVIRONMENT]


UNFOLD = {
    "SITE_TITLE": "Backend Admin",
    "SITE_HEADER": "Backend",
    "SITE_SYMBOL": "hub",  # material symbol shown next to the site header
    # ar/en switcher in the header (POSTs to the i18n/ urlconf in urls.py).
    "SHOW_LANGUAGES": True,
    "SHOW_BACK_BUTTON": True,
    "ENVIRONMENT": environment_callback,
    "ENVIRONMENT_TITLE_PREFIX": lambda request: f"[{env.ENVIRONMENT}] ",
    "BORDER_RADIUS": "8px",
    # Tailwind-style ramps as "R G B" strings; font tokens map onto base.
    "COLORS": {
        "base": {
            "50": "249 250 251",
            "100": "243 244 246",
            "200": "229 231 235",
            "300": "209 213 219",
            "400": "156 163 175",
            "500": "107 114 128",
            "600": "75 85 99",
            "700": "55 65 81",
            "800": "31 41 55",
            "900": "17 24 39",
            "950": "3 7 18",
        },
        "primary": {
            "50": "240 253 244",
            "100": "220 252 231",
            "200": "187 247 208",
            "300": "134 239 172",
            "400": "74 222 128",
            "500": "34 197 94",
            "600": "22 163 74",
            "700": "21 128 61",
            "800": "22 101 52",
            "900": "20 83 45",
            "950": "5 46 22",
        },
        "font": {
            "subtle-light": "var(--color-base-500)",
            "subtle-dark": "var(--color-base-400)",
            "default-light": "var(--color-base-600)",
            "default-dark": "var(--color-base-300)",
            "important-light": "var(--color-base-900)",
            "important-dark": "var(--color-base-100)",
        },
    },
    # Language flags on modeltranslation's per-field tabs.
    "EXTENSIONS": {
        "modeltranslation": {
            "flags": {
                "en": "🇬🇧",
                "ar": "🇸🇦",
            },
        },
    },
    "SIDEBAR": {
        "show_search": True,
        "show_all_applications": True,
        # Each item carries the matching view permission - links hide for
        # staff who cannot open them (consistency enforced by a G07 gate).
        "navigation": [
            {
                "title": _("Users"),
                "items": [
                    {
                        "title": _("Users"),
                        "icon": "person",
                        "link": reverse_lazy("admin:users_user_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "users.view_user"
                        ),
                    },
                    {
                        "title": _("Email addresses"),
                        "icon": "mail",
                        "link": reverse_lazy("admin:account_emailaddress_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "account.view_emailaddress"
                        ),
                    },
                    {
                        "title": _("Sessions"),
                        "icon": "devices",
                        "link": reverse_lazy(
                            "admin:usersessions_usersession_changelist"
                        ),
                        "permission": lambda request: request.user.has_perm(
                            "usersessions.view_usersession"
                        ),
                    },
                    {
                        "title": _("Groups"),
                        "icon": "group",
                        "link": reverse_lazy("admin:auth_group_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "auth.view_group"
                        ),
                    },
                ],
            },
            {
                "title": _("Operations"),
                "items": [
                    {
                        "title": _("Task results"),
                        "icon": "task_alt",
                        "link": reverse_lazy(
                            "admin:django_tasks_database_dbtaskresult_changelist"
                        ),
                        "permission": lambda request: request.user.has_perm(
                            "django_tasks_database.view_dbtaskresult"
                        ),
                    },
                    {
                        "title": _("Login attempts"),
                        "icon": "lock",
                        "link": reverse_lazy("admin:axes_accessattempt_changelist"),
                        "permission": lambda request: request.user.has_perm(
                            "axes.view_accessattempt"
                        ),
                    },
                ],
            },
        ],
    },
}

# --- Import-export -----------------------------------------------------------
# Operators pick columns per export run (SelectableFieldsExportForm); the
# offered file formats stay curated: CSV first (machines + the admin gate),
# XLSX for business users. Other tablib formats are menu noise.
EXPORT_FORMATS = [base_formats.CSV, base_formats.XLSX]

# --- Static / media (S3 via django-storages in production.py) --------------
STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

# --- Email ------------------------------------------------------------------
EMAIL_TIMEOUT = 5
DEFAULT_FROM_EMAIL = env.DEFAULT_FROM_EMAIL
EMAIL_HOST = env.EMAIL_HOST
EMAIL_PORT = env.EMAIL_PORT

# --- Security ----------------------------------------------------------------
X_FRAME_OPTIONS = "DENY"
SECURE_CSP = {
    "default-src": [CSP.SELF],
    "img-src": [CSP.SELF, "data:"],
    "font-src": [CSP.SELF, "data:"],
    # Admin widgets ship inline styles; scripts stay nonce-gated.
    "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
    "script-src": [CSP.SELF, CSP.NONCE],
}

# --- Cross-origin (browser SPA clients) ----------------------------------------
CORS_ALLOWED_ORIGINS = env.FRONTEND_ALLOWED_ORIGINS
CORS_ALLOW_CREDENTIALS = True
# Only the API surfaces are cross-origin; the admin (and anything else) must
# never carry credentialed CORS headers for the frontend origins.
CORS_URLS_REGEX = r"^/(api|_allauth)/"
CSRF_TRUSTED_ORIGINS = env.FRONTEND_ALLOWED_ORIGINS
CSRF_COOKIE_HTTPONLY = False  # deliberate: the SPA must read the CSRF cookie
if env.COOKIE_DOMAIN:
    SESSION_COOKIE_DOMAIN = env.COOKIE_DOMAIN
    CSRF_COOKIE_DOMAIN = env.COOKIE_DOMAIN

# --- Logging (structlog; local pretty-prints, production swaps to JSON) --------
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.filter_by_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    cache_logger_on_first_use=True,
)
LOGGING: dict[str, Any] = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "structlog": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(),
        },
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "structlog"},
    },
    "root": {"handlers": ["console"], "level": "INFO"},
}
