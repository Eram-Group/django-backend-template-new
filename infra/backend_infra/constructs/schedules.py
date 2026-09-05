"""EventBridge Scheduler -> ecs:RunTask, one schedule per management command.

Every schedule targets the worker task definition BY REVISION (a Scheduler
target pins an exact ARN). CDK binds the revision it registers; the deploy
workflow then advances every schedule in the group to the revision it
rolled out (``infra/scripts/roll_schedules.sh``), so scheduled jobs always
run the released code, never the last ``cdk deploy``'s. The execution role
is created here, once, and may run ANY revision of the family - otherwise
a schedule repointed by CD would be refused by a revision-pinned grant.
"""

from aws_cdk import ArnFormat
from aws_cdk import Duration
from aws_cdk import RemovalPolicy
from aws_cdk import Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
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
    role = _jobs_role(scope, cluster=cluster, task_definition=task_definition)
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
                role=role,
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


def _jobs_role(
    scope: Construct,
    *,
    cluster: ecs.ICluster,
    task_definition: ecs.FargateTaskDefinition,
) -> iam.Role:
    """The role Scheduler assumes to start a job. CDK's templated target
    grants the revision it knows; this grant covers every revision of the
    family so the deploy workflow can repoint the schedules."""
    role = iam.Role(
        scope,
        "JobsRole",
        assumed_by=iam.ServicePrincipal("scheduler.amazonaws.com"),
        description="EventBridge Scheduler execution role for the scheduled jobs",
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["ecs:RunTask"],
            resources=[
                Stack.of(scope).format_arn(
                    service="ecs",
                    resource="task-definition",
                    resource_name=f"{task_definition.family}:*",
                    arn_format=ArnFormat.SLASH_RESOURCE_NAME,
                )
            ],
            conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
        )
    )
    return role
