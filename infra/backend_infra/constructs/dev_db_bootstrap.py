"""One-off task that provisions an app database on the shared dev instance.

Nobody can reach the private RDS instance from outside the VPC, and secret
values must never pass through a laptop or a chat window - so the work runs
as a Fargate task: the master password is injected as an ECS secret, the
script creates role + database and writes the resulting DATABASE_URL straight
into the target environment's Secrets Manager secret.
"""

import base64
from pathlib import Path

from aws_cdk import Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from aws_cdk import aws_rds as rds
from constructs import Construct

from backend_infra.naming import MAIN_CONTAINER

SCRIPT = Path(__file__).resolve().parents[1] / "assets" / "dev_db_bootstrap.py"


class DevDbBootstrapTask(Construct):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        family: str,
        instance: rds.DatabaseInstance,
        vpc: ec2.IVpc,
        log_group: logs.ILogGroup,
    ) -> None:
        super().__init__(scope, construct_id)
        master = instance.secret
        assert master is not None  # noqa: S101 - generated credentials

        execution_role = iam.Role(
            self,
            "ExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )
        task_role = iam.Role(
            self, "TaskRole", assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
        )
        task_role.add_to_policy(
            iam.PolicyStatement(
                actions=[
                    "secretsmanager:GetSecretValue",
                    "secretsmanager:PutSecretValue",
                ],
                # Only per-environment app secrets (dev/<app>, staging/<app>).
                resources=[
                    f"arn:aws:secretsmanager:*:*:secret:{env}/*"
                    for env in ("dev", "staging")
                ],
            )
        )

        self.security_group = ec2.SecurityGroup(
            self,
            "Sg",
            vpc=vpc,
            allow_all_outbound=True,
            description="dev db bootstrap task",
        )
        self.task_definition = ecs.FargateTaskDefinition(
            self,
            "Task",
            family=family,
            cpu=256,
            memory_limit_mib=512,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
            execution_role=execution_role,
            task_role=task_role,
        )
        script_b64 = base64.b64encode(SCRIPT.read_bytes()).decode()
        self.task_definition.add_container(
            MAIN_CONTAINER,
            container_name=MAIN_CONTAINER,
            image=ecs.ContainerImage.from_registry(
                "public.ecr.aws/docker/library/python:3.14-slim"
            ),
            command=[
                "sh",
                "-c",
                (
                    'pip install -q "psycopg[binary]" boto3'
                    ' && echo "$SCRIPT_B64" | base64 -d | python -'
                ),
            ],
            environment={
                "MASTER_HOST": instance.db_instance_endpoint_address,
                "MASTER_USER": "postgres",
                "SCRIPT_B64": script_b64,
                "APP_DB": "override-me",
                "TARGET_SECRET": "override-me",
            },
            secrets={
                "MASTER_PASSWORD": ecs.Secret.from_secrets_manager(master, "password")
            },
            stop_timeout=Duration.seconds(30),
            logging=ecs.LogDrivers.aws_logs(
                log_group=log_group, stream_prefix="dev-db"
            ),
        )
