"""Worker tier: a plain Fargate service running ``manage.py db_worker``."""

from aws_cdk import aws_applicationautoscaling as appscaling
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
    scale_to_zero_overnight: bool,
) -> ecs.FargateService:
    """Always-on queue consumer.

    Spot is safe here: tasks are idempotent and ``db_worker`` finishes the
    current task on SIGTERM (Fargate gives two minutes' notice).
    """
    service = ecs.FargateService(
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
    if scale_to_zero_overnight:
        scalable = service.auto_scale_task_count(
            min_capacity=0, max_capacity=desired_count
        )
        scalable.scale_on_schedule(
            "Night",
            schedule=appscaling.Schedule.cron(hour="20", minute="0"),
            min_capacity=0,
            max_capacity=0,
        )
        scalable.scale_on_schedule(
            "Morning",
            schedule=appscaling.Schedule.cron(hour="5", minute="0"),
            min_capacity=desired_count,
            max_capacity=desired_count,
        )
    return service
