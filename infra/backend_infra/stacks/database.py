"""Stateful stack for a dedicated (production) database.

Kept apart from ``<app>-App-<env>`` so the stateless stack can be rolled back,
deleted and recreated freely while the instance - and its data - never move.
"""

from aws_cdk import CfnOutput
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from backend_infra import naming
from backend_infra.config import AppConfig
from backend_infra.config import EnvConfig
from backend_infra.constructs.database import DedicatedDatabase
from backend_infra.constructs.network import default_vpc


class DatabaseStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        app: AppConfig,
        env_config: EnvConfig,
        **kwargs: object,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.database = DedicatedDatabase(
            self,
            "Database",
            identifier=f"{env_config.name}-{app.name}",
            secret_prefix=naming.app_secret_name(app, env_config),
            vpc=default_vpc(self, app),
            security_group=ec2.SecurityGroup.from_security_group_id(
                self, "DbSg", app.db_security_group_id, mutable=False
            ),
        )
        CfnOutput(
            self, "Endpoint", value=self.database.instance.db_instance_endpoint_address
        )
        CfnOutput(self, "DatabaseUrlSecret", value=self.database.url_secret.secret_name)
