"""Worker tier: a plain Fargate service running ``manage.py db_worker``."""

from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from constructs import Construct


def worker_service(
    scope: Construct,
    construct_id: str,
    *,
    service_name: str,
    cluster: ecs.ICluster,
    task_definition: ecs.FargateTaskDefinition,
    security_group: ec2.ISecurityGroup,
    desired_count: int,
    spot: bool,
) -> ecs.FargateService:
    """Always-on queue consumer.

    Spot is safe here: tasks are idempotent and ``db_worker`` finishes the
    current task on SIGTERM (Fargate gives two minutes' notice).
    """
    return ecs.FargateService(
        scope,
        construct_id,
        service_name=service_name,
        cluster=cluster,
        task_definition=task_definition,
        desired_count=desired_count,
        assign_public_ip=True,
        vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
        security_groups=[security_group],
        capacity_provider_strategies=[
            ecs.CapacityProviderStrategy(
                capacity_provider="FARGATE_SPOT" if spot else "FARGATE", weight=1
            )
        ],
        circuit_breaker=ecs.DeploymentCircuitBreaker(enable=True, rollback=True),
        min_healthy_percent=0,
        max_healthy_percent=200,
        enable_execute_command=True,
        propagate_tags=ecs.PropagatedTagSource.SERVICE,
    )
