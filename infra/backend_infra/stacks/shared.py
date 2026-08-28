"""Account-level resources shared by every environment of the application."""

from aws_cdk import CfnOutput
from aws_cdk import Duration
from aws_cdk import RemovalPolicy
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_rds as rds
from constructs import Construct

from backend_infra.config import AppConfig
from backend_infra.constructs import database
from backend_infra.constructs import roles
from backend_infra.constructs.network import default_vpc
from backend_infra.constructs.network import public_subnets

GITHUB_OIDC_URL = "https://token.actions.githubusercontent.com"


class SharedStack(Stack):
    def __init__(
        self, scope: Construct, construct_id: str, *, app: AppConfig, **kwargs: object
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        self.app_config = app
        self.vpc = default_vpc(self, app)

        self.repository = ecr.Repository(
            self,
            "Repository",
            repository_name=f"eram/{app.name}",
            image_scan_on_push=True,
            image_tag_mutability=ecr.TagMutability.MUTABLE,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                ecr.LifecycleRule(
                    description="untagged layers",
                    tag_status=ecr.TagStatus.UNTAGGED,
                    max_image_age=Duration.days(7),
                ),
                ecr.LifecycleRule(
                    description="keep last 50 tagged images", max_image_count=50
                ),
            ],
        )

        self.cluster = ecs.Cluster(
            self,
            "Cluster",
            cluster_name=app.name,
            vpc=self.vpc,
            enable_fargate_capacity_providers=True,
            container_insights_v2=ecs.ContainerInsights.DISABLED,
        )

        self.infrastructure_role = roles.express_infrastructure_role(
            self, "ExpressInfrastructureRole"
        )

        if app.create_github_oidc_provider:
            provider = iam.OpenIdConnectProvider(
                self,
                "GithubOidc",
                url=GITHUB_OIDC_URL,
                client_ids=["sts.amazonaws.com"],
            )
            provider_arn = provider.open_id_connect_provider_arn
        else:
            provider_arn = (
                f"arn:aws:iam::{self.account}:oidc-provider/"
                "token.actions.githubusercontent.com"
            )
        self.deploy_role = roles.github_deploy_role(
            self,
            "GithubDeployRole",
            role_name=f"{app.name}-github-deploy",
            provider_arn=provider_arn,
            github_repo=app.github_repo,
            ecr_repository_arn=self.repository.repository_arn,
            passable_role_arns=[
                self.infrastructure_role.role_arn,
                # Per-env execution + task roles are CDK-named after their stack.
                f"arn:aws:iam::{self.account}:role/App-*",
            ],
        )

        self.db_security_group = database.database_security_group(
            self, "DbSg", self.vpc, app.vpc_cidr
        )
        self.shared_dev_db: rds.DatabaseInstance | None = None
        if app.create_shared_dev_db:
            self.shared_dev_db = database.shared_dev_instance(
                self,
                identifier=app.shared_dev_db_identifier,
                vpc=self.vpc,
                security_group=self.db_security_group,
            )
            CfnOutput(
                self,
                "SharedDevDbEndpoint",
                value=self.shared_dev_db.db_instance_endpoint_address,
            )

        CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        CfnOutput(self, "GithubDeployRoleArn", value=self.deploy_role.role_arn)
        CfnOutput(self, "PublicSubnets", value=",".join(public_subnets(app)))
        CfnOutput(
            self, "DbSecurityGroupId", value=self.db_security_group.security_group_id
        )


def vpc_of(stack: SharedStack) -> ec2.IVpc:
    return stack.vpc
