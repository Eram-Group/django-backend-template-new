"""Typed application settings loaded from the environment / ``.env``.

This module is the ONLY reader of ``os.environ`` in the codebase - Django
settings consume the ``env`` singleton exclusively. Every field has a safe
local default so the module imports with no ``.env`` present; fields that a
deployed environment must provide are enforced by the required-in-production
validator below (fail-fast at startup).
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

# Deliberate local-only sentinels, rejected outside ENVIRONMENT=local below.
_INSECURE_SECRET_KEY = "insecure-local-only-secret-key"  # noqa: S105
_INSECURE_SUPERUSER_PASSWORD = "admin"  # noqa: S105
_LOCAL_DATABASE_URL = "postgres://postgres:postgres@localhost:5432/backend"

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
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"
    SECRET_KEY: SecretStr = SecretStr(_INSECURE_SECRET_KEY)
    SECRET_KEY_FALLBACKS: CommaSeparated[SecretStr] = Field(default_factory=list)
    ALLOWED_HOSTS: CommaSeparated[str] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1"]
    )
    ADMIN_URL: str = "admin/"

    # Database
    DATABASE_URL: str = _LOCAL_DATABASE_URL
    DB_POOL_MIN_SIZE: int = 2
    DB_POOL_MAX_SIZE: int = 10
    DB_POOL_TIMEOUT: float = 10.0
    DB_POOL_MAX_LIFETIME: float = 3600.0
    DB_POOL_MAX_IDLE: float = 600.0

    # Superuser bootstrap (createsuperuser --noinput)
    DJANGO_SUPERUSER_EMAIL: str = "admin@example.com"
    DJANGO_SUPERUSER_PASSWORD: SecretStr = SecretStr(_INSECURE_SUPERUSER_PASSWORD)

    # Auth toggles
    ACCOUNT_ALLOW_REGISTRATION: bool = True
    SECURE_ADMIN_LOGIN: bool = False

    # Frontend / cross-origin
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    FRONTEND_ALLOWED_ORIGINS: CommaSeparated[str] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    COOKIE_DOMAIN: str | None = None

    # OAuth providers (absent creds degrade gracefully in local)
    GOOGLE_OAUTH_CLIENT_ID: str | None = None
    GOOGLE_OAUTH_CLIENT_SECRET: SecretStr | None = None
    APPLE_OAUTH_CLIENT_ID: str | None = None
    APPLE_OAUTH_TEAM_ID: str | None = None
    APPLE_OAUTH_KEY_ID: str | None = None
    APPLE_OAUTH_PRIVATE_KEY_B64: SecretStr | None = None  # base64-encoded PEM

    # Email
    DEFAULT_FROM_EMAIL: str = "noreply@localhost"
    EMAIL_HOST: str = "localhost"  # Mailpit in local dev
    EMAIL_PORT: int = 1025

    # AWS (S3 static/media, SES email)
    AWS_STORAGE_BUCKET_NAME: str | None = None
    AWS_S3_REGION_NAME: str | None = None
    AWS_S3_CUSTOM_DOMAIN: str | None = None  # CloudFront domain
    AWS_SES_REGION: str | None = None

    # Observability
    SENTRY_DSN: str | None = None
    SENTRY_TRACES_SAMPLE_RATE: float = 0.0

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

    @model_validator(mode="after")
    def _require_production_fields(self) -> Self:
        if self.ENVIRONMENT == "local":
            return self
        problems = [
            message
            for failed, message in (
                (
                    self.SECRET_KEY.get_secret_value() == _INSECURE_SECRET_KEY,
                    "SECRET_KEY must not be the insecure local default",
                ),
                (
                    self.DATABASE_URL == _LOCAL_DATABASE_URL,
                    "DATABASE_URL must not be the local default",
                ),
                (not self.ALLOWED_HOSTS, "ALLOWED_HOSTS must not be empty"),
                (
                    self.DJANGO_SUPERUSER_PASSWORD.get_secret_value()
                    == _INSECURE_SUPERUSER_PASSWORD,
                    "DJANGO_SUPERUSER_PASSWORD must not be the local default",
                ),
                (
                    not self.AWS_STORAGE_BUCKET_NAME,
                    "AWS_STORAGE_BUCKET_NAME is required",
                ),
                (not self.AWS_S3_REGION_NAME, "AWS_S3_REGION_NAME is required"),
                (not self.AWS_SES_REGION, "AWS_SES_REGION is required"),
                (not self.SENTRY_DSN, "SENTRY_DSN is required"),
            )
            if failed
        ]
        if problems:
            msg = f"Invalid {self.ENVIRONMENT} configuration: {'; '.join(problems)}"
            raise ValueError(msg)
        return self


env = Env()
