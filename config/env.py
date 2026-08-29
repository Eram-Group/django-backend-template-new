"""Typed application settings loaded from the environment / ``.env``.

This module is the ONLY reader of ``os.environ`` in the codebase - Django
settings consume the ``env`` singleton exclusively.

All fields are REQUIRED and carry no code defaults: local values come from
``.env`` (copy ``.env.example``), deployed values from the task-definition
environment. Missing values fail at import time, and pydantic lists every
missing field in one error.

``X | None`` fields are provider credentials: apps built from this template
use different providers, and an unset provider is absent - never a fallback.
Deployment-only fields (Sentry, S3/CloudFront, SES) are ``X | None`` for the
same reason locally, and ``_deployed_fields_present`` makes them required in
every deployed environment.
"""

from typing import Annotated
from typing import Literal
from typing import Self

from pydantic import Field
from pydantic import SecretStr
from pydantic import field_validator
from pydantic import model_validator
from pydantic_settings import BaseSettings
from pydantic_settings import NoDecode
from pydantic_settings import SettingsConfigDict

type CommaSeparated[T] = Annotated[list[T], NoDecode]

#: Deployment-only fields - present in every deployed environment.
_DEPLOYED_REQUIRED = (
    "AWS_STORAGE_BUCKET_NAME",
    "AWS_S3_REGION_NAME",
    "AWS_S3_CUSTOM_DOMAIN",
    "AWS_SES_REGION",
    "SENTRY_DSN",
    "SENTRY_RELEASE",
)

#: Which kind of gateway keys each environment may hold.
_PAYMENT_MODE_BY_ENVIRONMENT = {
    "local": "test",
    "dev": "test",
    "staging": "test",
    "production": "live",
}


class Env(BaseSettings):
    """Application environment, validated at import time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,  # FOO= in .env means unset, not empty string
        # Validation errors print the offending input by default - for a
        # settings model that is the raw env (SECRET_KEY, gateway keys, ...)
        # landing in a boot-time traceback. Field names are enough to fix it.
        hide_input_in_errors=True,
    )

    # Core
    ENVIRONMENT: Literal["local", "dev", "staging", "production"]
    SECRET_KEY: SecretStr
    # env_ignore_empty: `KEY=` is unset, which for a list means no items.
    SECRET_KEY_FALLBACKS: CommaSeparated[SecretStr] = Field(default_factory=list)
    ALLOWED_HOSTS: CommaSeparated[str]
    ADMIN_URL: str

    # Database
    DATABASE_URL: str
    DB_POOL_MIN_SIZE: int
    DB_POOL_MAX_SIZE: int
    DB_POOL_TIMEOUT: float
    DB_POOL_MAX_LIFETIME: float
    DB_POOL_MAX_IDLE: float

    # Superuser bootstrap (manage.py createsu)
    DJANGO_SUPERUSER_EMAIL: str
    DJANGO_SUPERUSER_PASSWORD: SecretStr

    # Frontend / cross-origin
    FRONTEND_BASE_URL: str
    FRONTEND_ALLOWED_ORIGINS: CommaSeparated[str]
    # Django two-state setting: None = host-only cookies (local), a parent
    # domain = shared across subdomains.
    COOKIE_DOMAIN: str | None = None

    # OAuth providers (absent creds = provider disabled)
    GOOGLE_OAUTH_CLIENT_ID: str | None = None
    GOOGLE_OAUTH_CLIENT_SECRET: SecretStr | None = None
    APPLE_OAUTH_CLIENT_ID: str | None = None
    APPLE_OAUTH_TEAM_ID: str | None = None
    APPLE_OAUTH_KEY_ID: str | None = None
    APPLE_OAUTH_PRIVATE_KEY_B64: SecretStr | None = None  # base64-encoded PEM

    # Email
    DEFAULT_FROM_EMAIL: str
    EMAIL_HOST: str
    EMAIL_PORT: int

    # Deployed-only (required whenever ENVIRONMENT != local, see the validator)
    AWS_STORAGE_BUCKET_NAME: str | None = None
    AWS_S3_REGION_NAME: str | None = None
    AWS_S3_CUSTOM_DOMAIN: str | None = None  # CloudFront domain
    AWS_SES_REGION: str | None = None
    SENTRY_DSN: str | None = None
    SENTRY_RELEASE: str | None = None  # git sha, injected by CD at render time
    SENTRY_TRACES_SAMPLE_RATE: float

    # Notifications: FCM push + SMS + WhatsApp providers (absent creds = the
    # provider is absent; tests swap in in-memory outboxes)
    FIREBASE_CREDENTIALS_B64: SecretStr | None = None  # base64 service-account JSON
    OURSMS_API_KEY: SecretStr | None = None
    OURSMS_SENDER: str | None = None
    SMSMISR_USERNAME: str | None = None
    SMSMISR_PASSWORD: SecretStr | None = None
    SMSMISR_SENDER: str | None = None
    # WhatsApp Cloud API (connector lands in a follow-up PR; the webhook
    # fields already gate the status endpoint)
    WHATSAPP_ACCESS_TOKEN: SecretStr | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None
    WHATSAPP_APP_SECRET: SecretStr | None = None  # X-Hub-Signature-256 HMAC key
    WHATSAPP_WEBHOOK_VERIFY_TOKEN: str | None = None  # GET handshake echo guard

    # Payments: Tap (SAR) / Paymob (EGP); local/test use the fake gateway
    BACKEND_BASE_URL: str  # absolute base for gateway webhook URLs
    TAP_SECRET_KEY: SecretStr | None = None
    PAYMOB_SECRET_KEY: SecretStr | None = None
    PAYMOB_PUBLIC_KEY: str | None = None
    PAYMOB_HMAC_SECRET: SecretStr | None = None
    # Dashboard API key (same for test/live) -> auth token for the transaction
    # inquiry API; unset = payment_verify/reconcile refuse loudly on paymob.
    PAYMOB_API_KEY: SecretStr | None = None
    PAYMOB_INTEGRATION_IDS: CommaSeparated[int] = Field(default_factory=list)
    # Card-on-file: one-click CIT checkout and MOTO for server-side MIT charges.
    PAYMOB_COF_INTEGRATION_ID: int | None = None
    PAYMOB_MOTO_INTEGRATION_ID: int | None = None

    @field_validator(
        "SECRET_KEY_FALLBACKS",
        "ALLOWED_HOSTS",
        "FRONTEND_ALLOWED_ORIGINS",
        "PAYMOB_INTEGRATION_IDS",
        mode="before",
    )
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _payment_keys_match_environment(self) -> Self:
        """Live gateway keys only in production, test keys everywhere else.

        Tap and Paymob keys carry the mode in their name (``sk_live_…`` /
        ``egy_sk_test_…``), and the key alone decides whether money moves.
        Unset keys mean the gateway is not configured and pass.
        """
        mode = _PAYMENT_MODE_BY_ENVIRONMENT[self.ENVIRONMENT]
        for name in ("TAP_SECRET_KEY", "PAYMOB_SECRET_KEY", "PAYMOB_PUBLIC_KEY"):
            value = getattr(self, name)
            if value is None:
                continue
            key = value.get_secret_value() if isinstance(value, SecretStr) else value
            if mode not in key:
                msg = f"{name} is not a {mode} key (ENVIRONMENT={self.ENVIRONMENT})"
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _deployed_fields_present(self) -> Self:
        """A deployed environment runs with S3, SES and Sentry - or not at all."""
        if self.ENVIRONMENT == "local":
            return self
        missing = [name for name in _DEPLOYED_REQUIRED if getattr(self, name) is None]
        if missing:
            msg = f"required when ENVIRONMENT={self.ENVIRONMENT}: {', '.join(missing)}"
            raise ValueError(msg)
        return self


# pydantic-settings fills the required fields from .env / the process environment.
# noinspection PyArgumentList
env = Env()
