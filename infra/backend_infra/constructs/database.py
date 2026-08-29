"""RDS for PostgreSQL 18 - one dedicated instance per production environment.

Dev/staging databases live on the account's shared instance, which is not
managed here (created once by hand - docs/DEPLOYMENT.md). ``DedicatedDatabase``
composes the ``DATABASE_URL`` secret from the generated master password so no
human ever copies one.
"""

from aws_cdk import Duration
from aws_cdk import Fn
from aws_cdk import RemovalPolicy
from aws_cdk import SecretValue
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_rds as rds
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct

# Characters that would need escaping inside a postgres:// URL.
URL_UNSAFE = " %+~`#$&*()|[]{}:;<>?!'/@\"\\"
POSTGRES_18 = rds.DatabaseInstanceEngine.postgres(
    version=rds.PostgresEngineVersion.of("18.4", "18")
)


def _instance(
    scope: Construct,
    construct_id: str,
    *,
    identifier: str,
    vpc: ec2.IVpc,
    security_group: ec2.ISecurityGroup,
    size: ec2.InstanceSize,
    backup_days: int,
    credentials: rds.Credentials,
    database_name: str | None,
) -> rds.DatabaseInstance:
    return rds.DatabaseInstance(
        scope,
        construct_id,
        instance_identifier=identifier,
        engine=POSTGRES_18,
        instance_type=ec2.InstanceType.of(ec2.InstanceClass.T4G, size),
        vpc=vpc,
        vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        publicly_accessible=False,
        security_groups=[security_group],
        allocated_storage=20,
        max_allocated_storage=100,
        storage_type=rds.StorageType.GP3,
        storage_encrypted=True,
        multi_az=False,
        database_name=database_name,
        credentials=credentials,
        backup_retention=Duration.days(backup_days),
        delete_automated_backups=False,
        deletion_protection=True,
        removal_policy=RemovalPolicy.RETAIN,
        auto_minor_version_upgrade=True,
        ca_certificate=rds.CaCertificate.RDS_CA_RSA2048_G1,
        cloudwatch_logs_exports=["postgresql"],
        enable_performance_insights=False,
    )


class DedicatedDatabase(Construct):
    """Production instance + ``DATABASE_URL`` secret composed from it."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        identifier: str,
        secret_prefix: str,
        vpc: ec2.IVpc,
        security_group: ec2.ISecurityGroup,
    ) -> None:
        super().__init__(scope, construct_id)
        # Created explicitly (not via Credentials.from_generated_secret) so the
        # removal policy lands on the secret itself: the instance is RETAINed
        # on stack deletion and its only password must be too, or the retained
        # database becomes unreachable.
        master = rds.DatabaseSecret(
            self,
            "MasterSecret",
            username="app",
            secret_name=f"{secret_prefix}/rds",
            exclude_characters=URL_UNSAFE,
        )
        master.apply_removal_policy(RemovalPolicy.RETAIN)
        self.instance = _instance(
            self,
            "Instance",
            identifier=identifier,
            vpc=vpc,
            security_group=security_group,
            size=ec2.InstanceSize.MICRO,
            backup_days=7,
            credentials=rds.Credentials.from_secret(master),
            database_name="app",
        )
        # The template only carries a {{resolve:secretsmanager}} dynamic
        # reference; CloudFormation materialises the URL into this secret.
        url = Fn.join(
            "",
            [
                "postgres://app:",
                master.secret_value_from_json("password").unsafe_unwrap(),
                "@",
                self.instance.db_instance_endpoint_address,
                ":5432/app",
            ],
        )
        self.url_secret = secretsmanager.Secret(
            self,
            "DatabaseUrl",
            secret_name=f"{secret_prefix}/database-url",
            description=f"DATABASE_URL for {identifier} (composed by CDK)",
            secret_string_value=SecretValue.unsafe_plain_text(url),
            removal_policy=RemovalPolicy.RETAIN,
        )
