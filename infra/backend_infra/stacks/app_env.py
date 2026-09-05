"""Everything one environment needs: storage, roles, web, worker, jobs.

Stateless by design - the dedicated production database lives in ``<app>-Db-<env>``
(``stacks/database.py``) so this stack can be torn down without touching data.
"""

from aws_cdk import ArnFormat
from aws_cdk import CfnOutput
from aws_cdk import Duration
from aws_cdk import RemovalPolicy
from aws_cdk import Stack
from aws_cdk import Tags
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from backend_infra import naming
from backend_infra.config import ENV_SECRET
from backend_infra.config import SCHEDULES
from backend_infra.config import AppConfig
from backend_infra.config import EnvConfig
from backend_infra.constructs import roles
from backend_infra.constructs.containers import WORKER_HEALTH_CHECK
from backend_infra.constructs.containers import fargate_task
from backend_infra.constructs.database import DedicatedDatabase
from backend_infra.constructs.network import public_subnets
from backend_infra.constructs.release_trigger import release_trigger
from backend_infra.constructs.schedules import scheduled_jobs
from backend_infra.constructs.storage import MediaStorage
from backend_infra.constructs.web_express import ExpressWebService
from backend_infra.constructs.worker import worker_service
from backend_infra.stacks.shared import SharedStack

WORKER_COMMAND = ["python", "manage.py", "db_worker", "--queue-name", "default,bulk"]


class AppEnvStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app: AppConfig,
        env_config: EnvConfig,
        shared: SharedStack,
        database: DedicatedDatabase | None,
        image_tag: str,
        sentry_release: str,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        cfg = env_config
        self.env_config = cfg
        Tags.of(self).add("env", cfg.name)
        self._retain = cfg.name == "production"
        subnet_ids = public_subnets(app)

        # --- Logs -------------------------------------------------------------
        web_logs = self._log_group("WebLogs", naming.log_group(app, cfg, "web"))
        worker_logs = self._log_group(
            "WorkerLogs", naming.log_group(app, cfg, "worker")
        )

        # --- Storage ------------------------------------------------------------
        origins = [
            o for o in cfg.plain_env.get("FRONTEND_ALLOWED_ORIGINS", "").split(",") if o
        ]
        if cfg.custom_domain:
            origins.append(f"https://{cfg.custom_domain}")
        retain = self._retain
        storage = MediaStorage(
            self,
            "Storage",
            bucket_name=naming.bucket_name(app, cfg),
            allowed_origins=origins or ["*"],
            retain=retain,
        )

        # --- Roles --------------------------------------------------------------
        ses_arn = (
            self.format_arn(
                service="ses",
                resource="identity",
                resource_name=app.ses_identity,
                arn_format=ArnFormat.SLASH_RESOURCE_NAME,
            )
            if app.ses_identity
            else None
        )
        task_role = roles.task_role(
            self, "TaskRole", ses_identity_arn=ses_arn, from_domain=app.ses_identity
        )
        # Per environment (not shared): granting it this env's secrets and log
        # groups from the Shared stack would create a cross-stack cycle.
        execution_role = roles.task_execution_role(self, "TaskExecutionRole")
        storage.bucket.grant_read_write(task_role)

        # --- Secrets ------------------------------------------------------------
        # Imported, never created: a CloudFormation-generated secret would be
        # regenerated on template changes and wipe hand-entered values.
        app_secret = secretsmanager.Secret.from_secret_name_v2(
            self, "AppSecret", naming.app_secret_name(app, cfg)
        )
        secrets = {
            key: ecs.Secret.from_secrets_manager(app_secret, key)
            for key in sorted(ENV_SECRET)
        }
        if cfg.database == "dedicated":
            assert database is not None  # noqa: S101 - app.py pairs them
            secrets["DATABASE_URL"] = ecs.Secret.from_secrets_manager(
                database.url_secret
            )

        environment = {
            **cfg.plain_env,
            "AWS_STORAGE_BUCKET_NAME": storage.bucket.bucket_name,
            "AWS_S3_REGION_NAME": self.region,
            "AWS_S3_CUSTOM_DOMAIN": storage.domain_name,
            "AWS_SES_REGION": self.region,
            "SENTRY_RELEASE": sentry_release,
        }

        # --- Task definitions ---------------------------------------------------
        common = {
            "execution_role": execution_role,
            "task_role": task_role,
            "repository": shared.repository,
            "image_tag": image_tag,
            "environment": environment,
            "secrets": secrets,
        }
        self.web_task = fargate_task(
            self,
            "WebTask",
            family=naming.web_family(app, cfg),
            cpu=cfg.web_cpu,
            memory=cfg.web_memory,
            log_group=web_logs,
            stream_prefix="web",
            command=None,  # image CMD = gunicorn
            web_port=8000,
            stop_timeout=Duration.seconds(30),
            health_check=None,  # the ALB probes /readyz
            **common,  # type: ignore[arg-type]
        )
        self.worker_task = fargate_task(
            self,
            "WorkerTask",
            family=naming.worker_family(app, cfg),
            cpu=cfg.worker_cpu,
            memory=cfg.worker_memory,
            log_group=worker_logs,
            stream_prefix="worker",
            command=WORKER_COMMAND,
            web_port=None,
            stop_timeout=Duration.seconds(120),  # >= the longest task
            health_check=WORKER_HEALTH_CHECK,
            **common,  # type: ignore[arg-type]
        )

        app_sg = ec2.SecurityGroup(
            self,
            "AppSg",
            vpc=shared.vpc,
            allow_all_outbound=True,
            description="worker + one-off tasks",
        )

        # --- Web (Express Mode) -------------------------------------------------
        self.web = ExpressWebService(
            self,
            "Web",
            service_name=naming.web_family(app, cfg),
            cluster_name=shared.cluster.cluster_name,
            infrastructure_role_arn=shared.infrastructure_role.role_arn,
            task_definition=self.web_task,
            subnet_ids=subnet_ids,
            min_tasks=cfg.web_min_tasks,
            max_tasks=cfg.web_max_tasks,
            cpu_target=cfg.web_cpu_target,
            tags={"app": app.name, "env": cfg.name},
        )
        if cfg.custom_domain:
            assert app.hosted_zone_id  # noqa: S101 - config invariant
            assert app.hosted_zone_name  # noqa: S101
            self.web.add_custom_domain(
                domain_name=cfg.custom_domain,
                hosted_zone_id=app.hosted_zone_id,
                hosted_zone_name=app.hosted_zone_name,
            )

        # --- Worker + scheduled jobs (egress-only SG) ----------------------------
        self.worker = worker_service(
            self,
            "Worker",
            service_name=naming.worker_family(app, cfg),
            cluster=shared.cluster,
            task_definition=self.worker_task,
            security_group=app_sg,
            desired_count=cfg.worker_count,
            spot=cfg.worker_spot,
        )
        # First deploy of an environment: migrate + cache table + static files
        # BEFORE any service task answers /readyz or drains the queue.
        release_trigger(
            self,
            "ReleaseTrigger",
            cluster=shared.cluster,
            task_definition=self.worker_task,
            subnet_ids=subnet_ids,
            security_group=app_sg,
            image_tag=image_tag,
            execute_before=[self.web, self.worker],
        )
        schedule_group = scheduled_jobs(
            self,
            group_name=f"{app.name}-{cfg.name}",
            jobs=SCHEDULES,
            cluster=shared.cluster,
            task_definition=self.worker_task,
            security_group=app_sg,
        )

        # --- Outputs: exactly what the GitHub environment variables need ----------
        CfnOutput(self, "WebEndpoint", value=self.web.endpoint)
        CfnOutput(self, "ExpressServiceArn", value=self.web.service_arn)
        CfnOutput(self, "ExpressServiceName", value=naming.web_family(app, cfg))
        CfnOutput(self, "WorkerServiceName", value=naming.worker_family(app, cfg))
        CfnOutput(self, "WebFamily", value=naming.web_family(app, cfg))
        CfnOutput(self, "WorkerFamily", value=naming.worker_family(app, cfg))
        CfnOutput(self, "ScheduleGroupName", value=schedule_group.schedule_group_name)
        CfnOutput(self, "Subnets", value=",".join(subnet_ids))
        CfnOutput(self, "SecurityGroup", value=app_sg.security_group_id)
        CfnOutput(self, "Bucket", value=storage.bucket.bucket_name)
        CfnOutput(self, "CloudFrontDomain", value=storage.domain_name)
        CfnOutput(self, "AppSecretName", value=naming.app_secret_name(app, cfg))

    def _log_group(self, construct_id: str, name: str) -> logs.LogGroup:
        return logs.LogGroup(
            self,
            construct_id,
            log_group_name=name,
            retention=logs.RetentionDays[self.env_config.log_retention],
            # Production keeps its logs through stack deletion; dev/staging
            # must be recreatable after a rolled-back deploy.
            removal_policy=RemovalPolicy.RETAIN
            if self._retain
            else RemovalPolicy.DESTROY,
        )
