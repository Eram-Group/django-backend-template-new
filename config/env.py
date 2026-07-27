"""Typed application settings loaded from the environment / ``.env``.

This module is the ONLY reader of ``os.environ`` in the codebase - Django
settings consume the ``env`` singleton exclusively.

All fields are REQUIRED and carry no code defaults: local values come from
``.env`` (copy ``.env.example``), deployed values from the task-definition
environment. Missing values fail at import time, and pydantic lists every
missing field in one error. ``X | None`` fields are the exception - they are
feature toggles whose absence disables the feature (OAuth provider, Sentry,
S3/CloudFront, cookie domain).
"""

from typing import Annotated
from typing import Literal
from typing import get_args

from pydantic import Field
from pydantic import SecretStr
from pydantic import ValidationInfo
from pydantic import field_validator
from pydantic_settings import BaseSettings
from pydantic_settings import NoDecode
from pydantic_settings import SettingsConfigDict

type CommaSeparated[T] = Annotated[list[T], NoDecode]


class Env(BaseSettings):
    """Application environment, validated at import time."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        env_ignore_empty=True,  # FOO= in .env means unset, not empty string
    )

    # Core
    ENVIRONMENT: Literal["local", "dev", "production"]
    SECRET_KEY: SecretStr
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

    # Tasks: true = run inline (local dev, no worker); false = DB queue + db_worker
    TASKS_IMMEDIATE: bool

    # Superuser bootstrap (createsuperuser --noinput)
    DJANGO_SUPERUSER_EMAIL: str
    DJANGO_SUPERUSER_PASSWORD: SecretStr

    # Auth toggles
    ACCOUNT_ALLOW_REGISTRATION: bool
    SECURE_ADMIN_LOGIN: bool

    # Frontend / cross-origin
    FRONTEND_BASE_URL: str
    FRONTEND_ALLOWED_ORIGINS: CommaSeparated[str]
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

    # AWS (S3 static/media, SES email) - production settings consume these
    AWS_STORAGE_BUCKET_NAME: str | None = None
    AWS_S3_REGION_NAME: str | None = None
    AWS_S3_CUSTOM_DOMAIN: str | None = None  # CloudFront domain
    AWS_SES_REGION: str | None = None

    # Observability (absent DSN = Sentry disabled)
    SENTRY_DSN: str | None = None
    SENTRY_RELEASE: str | None = None  # git sha, injected by CD at render time
    SENTRY_TRACES_SAMPLE_RATE: float

    # Payments: Tap (SAR) / Paymob (EGP); local/test use the fake gateway
    BACKEND_BASE_URL: str  # absolute base for gateway webhook URLs
    TAP_SECRET_KEY: SecretStr | None = None
    PAYMOB_SECRET_KEY: SecretStr | None = None
    PAYMOB_PUBLIC_KEY: str | None = None
    PAYMOB_HMAC_SECRET: SecretStr | None = None
    PAYMOB_INTEGRATION_IDS: CommaSeparated[int] = Field(default_factory=list)
    # Card-on-file: one-click CIT checkout (unset = fall back to
    # PAYMOB_INTEGRATION_IDS, which works in Paymob test mode) and MOTO for
    # server-side MIT charges (unset = MIT refused on paymob).
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

    @field_validator("*", mode="before")
    @classmethod
    def _blank_optional_is_unset(cls, value: object, info: ValidationInfo) -> object:
        """Treat a blank string as unset on the optional feature-toggle fields.

        ``env_ignore_empty`` only catches an *exactly* empty value, so a
        whitespace-only one parses as a real ``SecretStr('   ')`` and slips
        past every ``is None`` not-configured guard downstream - surfacing far
        away as a decode error at send time, or worse: ``paymob._expected_hmac``
        computes a signature with a blank key instead of refusing the webhook.

        Only fields that already permit ``None`` are normalised, so required
        fields keep failing loudly at import and the ``CommaSeparated`` lists
        (never optional) still collapse to an empty list.
        """
        if not isinstance(value, str) or value.strip():
            return value
        field = cls.model_fields.get(info.field_name or "")
        if field is None or type(None) not in get_args(field.annotation):
            return value
        return None


# pydantic-settings fills the required fields from .env / the process environment.
# noinspection PyArgumentList
env = Env()
