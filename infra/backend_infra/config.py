"""Single source of truth for the deployment topology.

One ``AppConfig`` per application (account, region, repo, network); one
``EnvConfig`` per environment. Everything the stacks need is declared here -
no CDK context lookups at synth time, so ``cdk synth`` and the tests run
without AWS credentials.

Env-var ownership (mirrors ``.env.example`` - ``tests/test_env_coverage.py``
fails when the two drift):

* ``ENV_PLAIN``      - non-secret values from ``EnvConfig.plain_env``
* ``ENV_FROM_STACK`` - values the stack knows (bucket, CloudFront, region)
* ``ENV_SECRET``     - JSON keys of the ``<env>/<app>`` Secrets Manager secret
* ``SENTRY_RELEASE`` - CD injects it per deploy (``-c sentry_release`` in CDK)
"""

from dataclasses import dataclass
from dataclasses import field
from typing import Literal

type EnvName = Literal["dev", "staging", "production"]
type DatabaseKind = Literal["shared", "dedicated"]


@dataclass(frozen=True)
class Subnet:
    subnet_id: str
    availability_zone: str


@dataclass(frozen=True, kw_only=True)
class AppConfig:
    """Account-level facts for one application."""

    name: str
    account: str
    region: str
    github_repo: str  # "org/repo" - scopes the OIDC deploy role trust
    vpc_id: str
    vpc_cidr: str
    public_subnets: tuple[Subnet, ...]  # one per AZ - the Express ALB pins these
    hosted_zone_id: str | None = None
    hosted_zone_name: str | None = None
    ses_identity: str | None = None  # verified SES domain the task role may send from
    # Account-level resources that exist ONCE per AWS account and are only
    # referenced here - never created - so every app copied from this
    # template shares them without fighting over ownership
    # (docs/DEPLOYMENT.md "Account prerequisites"). The shared dev RDS
    # instance is one too: apps reach it only through DATABASE_URL.
    github_oidc_provider_arn: str
    db_security_group_id: str  # allows 5432 from the VPC; on every RDS instance


@dataclass(frozen=True)
class EnvConfig:
    """One deployable environment of the application."""

    name: EnvName
    web_cpu: int
    web_memory: int
    web_max_tasks: int
    worker_spot: bool
    database: DatabaseKind
    custom_domain: str | None = None
    worker_cpu: int = 256
    worker_memory: int = 512
    plain_env: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ScheduledJob:
    """A management command run on a cron by EventBridge Scheduler."""

    name: str
    cron: str  # minute hour day month weekday year - EventBridge 6-field cron (UTC)
    command: tuple[str, ...]
    enabled: bool = True


# --- Env-var ownership --------------------------------------------------------

ENV_PLAIN: frozenset[str] = frozenset(
    {
        "ENVIRONMENT",
        "SECRET_KEY_FALLBACKS",
        "ALLOWED_HOSTS",
        "ADMIN_URL",
        "DB_POOL_MIN_SIZE",
        "DB_POOL_MAX_SIZE",
        "DB_POOL_TIMEOUT",
        "DB_POOL_MAX_LIFETIME",
        "DB_POOL_MAX_IDLE",
        "TASKS_IMMEDIATE",
        "DJANGO_SUPERUSER_EMAIL",
        "ACCOUNT_ALLOW_REGISTRATION",
        "SECURE_ADMIN_LOGIN",
        "FRONTEND_BASE_URL",
        "FRONTEND_ALLOWED_ORIGINS",
        "COOKIE_DOMAIN",
        "GOOGLE_OAUTH_CLIENT_ID",
        "APPLE_OAUTH_CLIENT_ID",
        "APPLE_OAUTH_TEAM_ID",
        "APPLE_OAUTH_KEY_ID",
        "DEFAULT_FROM_EMAIL",
        "EMAIL_HOST",
        "EMAIL_PORT",
        "SENTRY_TRACES_SAMPLE_RATE",
        "OURSMS_SENDER",
        "SMSMISR_USERNAME",
        "SMSMISR_SENDER",
        "WHATSAPP_PHONE_NUMBER_ID",
        "BACKEND_BASE_URL",
        "PAYMOB_PUBLIC_KEY",
        "PAYMOB_INTEGRATION_IDS",
        "PAYMOB_COF_INTEGRATION_ID",
        "PAYMOB_MOTO_INTEGRATION_ID",
    }
)

ENV_FROM_STACK: frozenset[str] = frozenset(
    {
        "AWS_STORAGE_BUCKET_NAME",
        "AWS_S3_REGION_NAME",
        "AWS_S3_CUSTOM_DOMAIN",
        "AWS_SES_REGION",
    }
)

ENV_SECRET: frozenset[str] = frozenset(
    {
        "SECRET_KEY",
        "DATABASE_URL",
        "DJANGO_SUPERUSER_PASSWORD",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "APPLE_OAUTH_PRIVATE_KEY_B64",
        "FIREBASE_CREDENTIALS_B64",
        "SENTRY_DSN",
        "OURSMS_API_KEY",
        "SMSMISR_PASSWORD",
        "WHATSAPP_ACCESS_TOKEN",
        "WHATSAPP_APP_SECRET",
        "WHATSAPP_WEBHOOK_VERIFY_TOKEN",
        "TAP_SECRET_KEY",
        "PAYMOB_SECRET_KEY",
        "PAYMOB_HMAC_SECRET",
        "PAYMOB_API_KEY",
    }
)

ENV_FROM_CD: frozenset[str] = frozenset({"SENTRY_RELEASE"})

# --- Scheduled jobs (cadence lives here, next to the commands it triggers) ------

SCHEDULES: tuple[ScheduledJob, ...] = (
    ScheduledJob(
        "clearsessions", "0 3 * * ? *", ("python", "manage.py", "clearsessions")
    ),
    ScheduledJob(
        "prune-task-results",
        "30 3 * * ? *",
        ("python", "manage.py", "prune_db_task_results", "--min-age-days", "14"),
    ),
    ScheduledJob(
        "reconcile-payments",
        "*/10 * * * ? *",
        ("python", "manage.py", "reconcile_payments"),
    ),
    ScheduledJob(
        "sweep-deliveries",
        "*/15 * * * ? *",
        ("python", "manage.py", "sweep_deliveries"),
    ),
    ScheduledJob(
        "sample-scheduled-job",
        "0 4 * * ? *",
        ("python", "manage.py", "sample_scheduled_job"),
        enabled=False,  # template reference only
    ),
)

# --- This application ------------------------------------------------------------

APP = AppConfig(
    name="template",
    account="975049989256",
    region="eu-central-1",
    github_repo="Eram-Group/django-backend-template-new",
    vpc_id="vpc-01a4ccaae4f845880",
    vpc_cidr="172.31.0.0/16",
    public_subnets=(
        Subnet("subnet-0c48a975832562fb2", "eu-central-1a"),
        Subnet("subnet-0e8196342913d84f1", "eu-central-1b"),
        Subnet("subnet-038a2312a50f0b6bc", "eu-central-1c"),
    ),
    hosted_zone_id="Z05566011AELTZM3HIA2I",
    hosted_zone_name="eramapps.com",
    ses_identity="eramapps.com",
    github_oidc_provider_arn=(
        "arn:aws:iam::975049989256:oidc-provider/token.actions.githubusercontent.com"
    ),
    db_security_group_id="sg-0ebcaa5f2e9f3d3fd",
)


def _plain_env(*, environment: EnvName, base_url: str, hosts: str) -> dict[str, str]:
    """Non-secret settings; override per environment below."""
    return {
        "ENVIRONMENT": environment,
        "SECRET_KEY_FALLBACKS": "",
        "ALLOWED_HOSTS": hosts,
        "ADMIN_URL": "manage-4f9c2b/",
        "DB_POOL_MIN_SIZE": "1",
        "DB_POOL_MAX_SIZE": "4",
        "DB_POOL_TIMEOUT": "10",
        "DB_POOL_MAX_LIFETIME": "1800",
        "DB_POOL_MAX_IDLE": "300",
        "TASKS_IMMEDIATE": "false",
        "DJANGO_SUPERUSER_EMAIL": "admin@eramapps.com",
        "ACCOUNT_ALLOW_REGISTRATION": "true",
        "SECURE_ADMIN_LOGIN": "false",
        "FRONTEND_BASE_URL": base_url,
        "FRONTEND_ALLOWED_ORIGINS": base_url,
        "COOKIE_DOMAIN": "",
        "GOOGLE_OAUTH_CLIENT_ID": "",
        "APPLE_OAUTH_CLIENT_ID": "",
        "APPLE_OAUTH_TEAM_ID": "",
        "APPLE_OAUTH_KEY_ID": "",
        "DEFAULT_FROM_EMAIL": "no-reply@eramapps.com",
        # Required by the env contract; SES replaces SMTP when deployed.
        "EMAIL_HOST": "localhost",
        "EMAIL_PORT": "25",
        "SENTRY_TRACES_SAMPLE_RATE": "0.1",
        "OURSMS_SENDER": "",
        "SMSMISR_USERNAME": "",
        "SMSMISR_SENDER": "",
        "WHATSAPP_PHONE_NUMBER_ID": "",
        "BACKEND_BASE_URL": base_url,
        "PAYMOB_PUBLIC_KEY": "",
        "PAYMOB_INTEGRATION_IDS": "",
        "PAYMOB_COF_INTEGRATION_ID": "",
        "PAYMOB_MOTO_INTEGRATION_ID": "",
    }


# Express Mode hands out https://<service>-<hash>.ecs.<region>.on.aws; the
# suffix wildcard covers it before the hostname is known.
_EXPRESS_HOSTS = ".ecs.eu-central-1.on.aws"

ENVIRONMENTS: dict[EnvName, EnvConfig] = {
    "dev": EnvConfig(
        name="dev",
        web_cpu=256,
        web_memory=1024,
        web_max_tasks=2,
        worker_spot=True,
        database="shared",
        plain_env=_plain_env(
            environment="dev",
            base_url="https://dev.template.eramapps.com",
            hosts=_EXPRESS_HOSTS,
        )
        | {"SENTRY_TRACES_SAMPLE_RATE": "1.0"},
    ),
    "production": EnvConfig(
        name="production",
        web_cpu=512,
        web_memory=1024,
        web_max_tasks=6,
        worker_spot=False,
        database="dedicated",
        custom_domain="api.template.eramapps.com",
        plain_env=_plain_env(
            environment="production",
            base_url="https://template.eramapps.com",
            hosts=f"api.template.eramapps.com,{_EXPRESS_HOSTS}",
        )
        | {"BACKEND_BASE_URL": "https://api.template.eramapps.com"},
    ),
}
