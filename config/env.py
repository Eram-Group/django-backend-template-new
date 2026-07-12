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

from pydantic import Field
from pydantic import SecretStr
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

    # Notifications: FCM push + SMS providers (absent creds = not configured;
    # local/test use console/locmem backends regardless)
    FIREBASE_CREDENTIALS_B64: SecretStr | None = None  # base64 service-account JSON
    OURSMS_API_KEY: SecretStr | None = None
    OURSMS_SENDER: str | None = None
    SMSMISR_USERNAME: str | None = None
    SMSMISR_PASSWORD: SecretStr | None = None
    SMSMISR_SENDER: str | None = None

    @field_validator(
        "SECRET_KEY_FALLBACKS",
        "ALLOWED_HOSTS",
        "FRONTEND_ALLOWED_ORIGINS",
        mode="before",
    )
    @classmethod
    def _split_comma_separated(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value


# pydantic-settings fills the required fields from .env / the process environment.
# noinspection PyArgumentList
env = Env()
