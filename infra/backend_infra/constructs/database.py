"""RDS for PostgreSQL 18 - the only database engine this stack knows.

``shared_dev_instance`` is the one instance every dev/staging environment of
every app shares (their databases are created by hand on it);
``dedicated_instance`` is one instance per production app, with a CDK-composed
``DATABASE_URL`` secret so no human ever copies a password.
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


def database_security_group(
    scope: Construct, construct_id: str, vpc: ec2.IVpc, cidr: str
) -> ec2.SecurityGroup:
    sg = ec2.SecurityGroup(
        scope,
        construct_id,
        vpc=vpc,
        allow_all_outbound=False,
        description="Postgres from the VPC",
    )
    sg.add_ingress_rule(
        ec2.Peer.ipv4(cidr), ec2.Port.tcp(5432), "postgres from the VPC"
    )
    return sg


def _instance(
    scope: Construct,
    construct_id: str,
    *,
    identifier: str,
    vpc: ec2.IVpc,
    security_group: ec2.ISecurityGroup,
    size: ec2.InstanceSize,
    backup_days: int,
    username: str,
    secret_name: str,
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
        credentials=rds.Credentials.from_generated_secret(
            username, secret_name=secret_name, exclude_characters=URL_UNSAFE
        ),
        backup_retention=Duration.days(backup_days),
        delete_automated_backups=False,
        deletion_protection=True,
        removal_policy=RemovalPolicy.RETAIN,
        auto_minor_version_upgrade=True,
        ca_certificate=rds.CaCertificate.RDS_CA_RSA2048_G1,
        cloudwatch_logs_exports=["postgresql"],
        enable_performance_insights=False,
    )


def shared_dev_instance(
    scope: Construct,
    *,
    identifier: str,
    vpc: ec2.IVpc,
    security_group: ec2.ISecurityGroup,
) -> rds.DatabaseInstance:
    return _instance(
        scope,
        "SharedDevDb",
        identifier=identifier,
        vpc=vpc,
        security_group=security_group,
        size=ec2.InstanceSize.SMALL,
        backup_days=1,
        username="postgres",
        secret_name=f"shared/{identifier}/master",
        database_name=None,
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
        self.instance = _instance(
            self,
            "Instance",
            identifier=identifier,
            vpc=vpc,
            security_group=security_group,
            size=ec2.InstanceSize.MICRO,
            backup_days=7,
            username="app",
            secret_name=f"{secret_prefix}/rds",
            database_name="app",
        )
        master = self.instance.secret
        assert master is not None  # noqa: S101 - generated above
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
        )
