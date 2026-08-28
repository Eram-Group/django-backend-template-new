"""Per-application resources shared by every environment: ECR, cluster, roles.

Account-level resources (GitHub OIDC provider, shared dev RDS, DB security
group) are NOT here - they exist once per account and ``AppConfig`` only
references them, so the many apps copied from this template never compete
for their ownership.
"""

from aws_cdk import CfnOutput
from aws_cdk import Duration
from aws_cdk import RemovalPolicy
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_logs as logs
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

from backend_infra.config import AppConfig
from backend_infra.constructs import roles
from backend_infra.constructs.dev_db_bootstrap import DevDbBootstrapTask
from backend_infra.constructs.network import default_vpc
from backend_infra.constructs.network import public_subnets


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
        self.deploy_role = roles.github_deploy_role(
            self,
            "GithubDeployRole",
            role_name=f"{app.name}-github-deploy",
            provider_arn=app.github_oidc_provider_arn,
            github_repo=app.github_repo,
            ecr_repository_arn=self.repository.repository_arn,
            passable_role_arns=[
                self.infrastructure_role.role_arn,
                # Per-env execution + task roles are CDK-named after their stack.
                f"arn:aws:iam::{self.account}:role/App-*",
            ],
        )

        # One-off task that creates this app's dev/staging databases on the
        # shared instance (per app: its family carries the app name).
        bootstrap_family = f"{app.name}-dev-db-bootstrap"
        bootstrap = DevDbBootstrapTask(
            self,
            "DevDbBootstrap",
            family=bootstrap_family,
            host=app.dev_db_host,
            master_secret=secretsmanager.Secret.from_secret_name_v2(
                self, "DevDbMasterSecret", app.dev_db_master_credentials
            ),
            vpc=self.vpc,
            log_group=logs.LogGroup(
                self,
                "DevDbBootstrapLogs",
                log_group_name=f"/aws/ecs/{bootstrap_family}",
                retention=logs.RetentionDays.ONE_MONTH,
                removal_policy=RemovalPolicy.DESTROY,
            ),
        )

        CfnOutput(self, "RepositoryUri", value=self.repository.repository_uri)
        CfnOutput(self, "ClusterName", value=self.cluster.cluster_name)
        CfnOutput(self, "GithubDeployRoleArn", value=self.deploy_role.role_arn)
        CfnOutput(self, "PublicSubnets", value=",".join(public_subnets(app)))
        CfnOutput(self, "DevDbBootstrapFamily", value=bootstrap.task_definition.family)
        CfnOutput(
            self,
            "DevDbBootstrapSecurityGroupId",
            value=bootstrap.security_group.security_group_id,
        )


def vpc_of(stack: SharedStack) -> ec2.IVpc:
    return stack.vpc
