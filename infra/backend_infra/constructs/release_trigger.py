"""Run the release step (check, migrate, createcachetable, collectstatic) as
part of the stack deployment, before the services exist."""

from pathlib import Path

from aws_cdk import Duration
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import triggers
from constructs import Construct

RELEASE_COMMAND = (
    "python manage.py check --deploy --fail-level WARNING"
    " && python manage.py migrate --noinput"
    " && python manage.py createcachetable"
    " && python manage.py seed_notification_config"
    " && python manage.py collectstatic --noinput"
)
HANDLER = Path(__file__).resolve().parents[1] / "assets" / "release_trigger.py"


def release_trigger(
    scope: Construct,
    construct_id: str,
    *,
    cluster: ecs.ICluster,
    task_definition: ecs.FargateTaskDefinition,
    subnet_ids: list[str],
    security_group: ec2.ISecurityGroup,
    image_tag: str,
    execute_before: list[Construct],
) -> triggers.TriggerFunction:
    fn = triggers.TriggerFunction(
        scope,
        construct_id,
        runtime=lambda_.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=lambda_.Code.from_inline(HANDLER.read_text()),
        timeout=Duration.minutes(15),
        environment={
            "CLUSTER": cluster.cluster_name,
            "TASK_DEFINITION": task_definition.task_definition_arn,
            "SUBNETS": ",".join(subnet_ids),
            "SECURITY_GROUP": security_group.security_group_id,
            "COMMAND": RELEASE_COMMAND,
            "IMAGE_TAG": image_tag,  # re-run whenever the pinned image changes
        },
        execute_before=execute_before,
        execute_on_handler_change=True,
    )
    fn.add_to_role_policy(
        iam.PolicyStatement(
            actions=["ecs:RunTask", "ecs:DescribeTasks"],
            resources=["*"],
            conditions={"ArnEquals": {"ecs:cluster": cluster.cluster_arn}},
        )
    )
    task_definition.execution_role.grant_pass_role(fn)  # type: ignore[union-attr]
    task_definition.task_role.grant_pass_role(fn)
    return fn
