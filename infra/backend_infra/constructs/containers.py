"""Task definitions: one image, container ``Main``, ARM64 Fargate."""

from aws_cdk import Duration
from aws_cdk import Size
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_logs as logs
from constructs import Construct

from backend_infra.naming import MAIN_CONTAINER

ARM64 = ecs.RuntimePlatform(
    cpu_architecture=ecs.CpuArchitecture.ARM64,
    operating_system_family=ecs.OperatingSystemFamily.LINUX,
)

# The worker has no port for the ALB to probe, so ECS never learned that a
# task lost its database (rotated secret, security-group change) - it just
# sat RUNNING with an empty queue. One Django boot + connection a minute is
# the price of a real liveness signal on a 0.25 vCPU task.
WORKER_HEALTH_CHECK = ecs.HealthCheck(
    command=[
        "CMD-SHELL",
        (
            "python -c 'import django; django.setup(); "
            "from django.db import connection; connection.ensure_connection()'"
        ),
    ],
    interval=Duration.seconds(60),
    timeout=Duration.seconds(15),
    retries=3,
    start_period=Duration.seconds(90),
)


def fargate_task(
    scope: Construct,
    construct_id: str,
    *,
    family: str,
    cpu: int,
    memory: int,
    execution_role: iam.IRole,
    task_role: iam.IRole,
    repository: ecr.IRepository,
    image_tag: str,
    environment: dict[str, str],
    secrets: dict[str, ecs.Secret],
    log_group: logs.ILogGroup,
    stream_prefix: str,
    command: list[str] | None,
    web_port: int | None,
    stop_timeout: Duration,
    health_check: ecs.HealthCheck | None,
) -> ecs.FargateTaskDefinition:
    """Register a Fargate task definition whose only container is ``Main``.

    ``web_port`` adds the single named TCP port mapping Express Mode requires;
    ``command=None`` keeps the image CMD (gunicorn). ``health_check`` is the
    container-level probe: the web tier is probed by the ALB and passes None,
    the worker (no port) gets ``WORKER_HEALTH_CHECK``.
    """
    task = ecs.FargateTaskDefinition(
        scope,
        construct_id,
        family=family,
        cpu=cpu,
        memory_limit_mib=memory,
        runtime_platform=ARM64,
        execution_role=execution_role,
        task_role=task_role,
    )
    task.add_container(
        MAIN_CONTAINER,
        container_name=MAIN_CONTAINER,
        image=ecs.ContainerImage.from_ecr_repository(repository, image_tag),
        command=command,
        # manage.py defaults to the local settings module (debug toolbar);
        # every deployed run mode must run the production module.
        environment={
            "DJANGO_SETTINGS_MODULE": "config.settings.production",
            **environment,
        },
        secrets=secrets,
        essential=True,
        stop_timeout=stop_timeout,
        health_check=health_check,
        linux_parameters=ecs.LinuxParameters(
            scope, f"{construct_id}Init", init_process_enabled=True
        ),
        port_mappings=(
            [
                ecs.PortMapping(
                    container_port=web_port,
                    name="web",
                    protocol=ecs.Protocol.TCP,
                    app_protocol=ecs.AppProtocol.http,
                )
            ]
            if web_port
            else None
        ),
        logging=ecs.LogDrivers.aws_logs(
            log_group=log_group,
            stream_prefix=stream_prefix,
            mode=ecs.AwsLogDriverMode.NON_BLOCKING,
            max_buffer_size=Size.mebibytes(25),
        ),
    )
    return task
