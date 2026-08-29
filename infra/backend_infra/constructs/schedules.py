"""EventBridge Scheduler -> ecs:RunTask, one schedule per management command."""

from aws_cdk import Duration
from aws_cdk import RemovalPolicy
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_scheduler as scheduler
from aws_cdk import aws_scheduler_targets as targets
from constructs import Construct

from backend_infra.config import ScheduledJob
from backend_infra.naming import MAIN_CONTAINER


def _expression(cron: str) -> scheduler.ScheduleExpression:
    """Six-field EventBridge cron (minute hour day month weekday year), UTC."""
    if len(cron.split()) != 6:
        msg = f"expected 6 cron fields, got {cron!r}"
        raise ValueError(msg)
    return scheduler.ScheduleExpression.expression(f"cron({cron})")


def scheduled_jobs(
    scope: Construct,
    *,
    group_name: str,
    jobs: tuple[ScheduledJob, ...],
    cluster: ecs.ICluster,
    task_definition: ecs.FargateTaskDefinition,
    security_group: ec2.ISecurityGroup,
) -> scheduler.ScheduleGroup:
    group = scheduler.ScheduleGroup(
        scope,
        "Jobs",
        schedule_group_name=group_name,
        removal_policy=RemovalPolicy.DESTROY,
    )
    for job in jobs:
        scheduler.Schedule(
            scope,
            f"Job-{job.name}",
            schedule_name=f"{group_name}-{job.name}",
            schedule_group=group,
            schedule=_expression(job.cron),
            description=" ".join(job.command),
            # Sweeps are idempotent and frequent: never stack a retry on top
            # of the next tick, and drop invocations older than five minutes.
            time_window=scheduler.TimeWindow.off(),
            target=targets.EcsRunFargateTask(
                cluster,
                task_definition=task_definition,
                vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
                security_groups=[security_group],
                assign_public_ip=True,
                capacity_provider_strategies=[
                    ecs.CapacityProviderStrategy(capacity_provider="FARGATE", weight=1)
                ],
                propagate_tags=True,
                input=scheduler.ScheduleTargetInput.from_object(
                    {
                        "containerOverrides": [
                            {"name": MAIN_CONTAINER, "command": list(job.command)}
                        ]
                    }
                ),
                retry_attempts=0,
                max_event_age=Duration.minutes(5),
            ),
        )
    return group
