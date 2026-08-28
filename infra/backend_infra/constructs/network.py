"""The account's default VPC, referenced by attributes (no context lookup)."""

from aws_cdk import aws_ec2 as ec2
from constructs import Construct

from backend_infra.config import AppConfig


def default_vpc(scope: Construct, app: AppConfig) -> ec2.IVpc:
    return ec2.Vpc.from_vpc_attributes(
        scope,
        "Vpc",
        vpc_id=app.vpc_id,
        vpc_cidr_block=app.vpc_cidr,
        availability_zones=[s.availability_zone for s in app.public_subnets],
        public_subnet_ids=[s.subnet_id for s in app.public_subnets],
    )


def public_subnets(app: AppConfig) -> list[str]:
    return [s.subnet_id for s in app.public_subnets]
