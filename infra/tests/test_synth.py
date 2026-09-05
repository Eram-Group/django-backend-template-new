"""Synth every stack and assert the load-bearing properties."""

import aws_cdk as cdk
import pytest
from aws_cdk import assertions
from backend_infra.config import APP
from backend_infra.config import ENVIRONMENTS
from backend_infra.synth import build_app


@pytest.fixture(scope="module")
def stacks() -> dict[str, cdk.Stack]:
    """Exactly the graph app.py deploys, keyed by the stack-name suffix."""
    app = build_app(image_tag="synth", sentry_release="synth")
    prefix = f"{APP.name}-"
    return {
        child.stack_name.removeprefix(prefix): child
        for child in app.node.children
        if isinstance(child, cdk.Stack)
    }


@pytest.fixture(scope="module")
def templates(stacks: dict[str, cdk.Stack]) -> dict[str, assertions.Template]:
    return {
        name: assertions.Template.from_stack(stack) for name, stack in stacks.items()
    }


def test_stateful_and_production_stacks_are_termination_protected(
    stacks: dict[str, cdk.Stack],
) -> None:
    protected = {name for name, stack in stacks.items() if stack.termination_protection}
    assert protected == {"Shared", "Db-production", "App-production"}


def test_shared_owns_cluster_repo_and_deploy_role(
    templates: dict[str, assertions.Template],
) -> None:
    t = templates["Shared"]
    t.resource_count_is("AWS::ECS::Cluster", 1)
    t.has_resource_properties(
        "AWS::ECR::Repository", {"RepositoryName": f"eram/{APP.name}"}
    )
    t.has_resource_properties(
        "AWS::IAM::Role", {"RoleName": f"{APP.name}-github-deploy"}
    )
    t.resource_count_is("AWS::ECS::TaskDefinition", 0)


def test_account_level_resources_are_referenced_not_created(
    templates: dict[str, assertions.Template],
) -> None:
    """Ten apps copy this template: none may own the OIDC provider or dev DB."""
    for t in templates.values():
        assert not t.find_resources("Custom::AWSCDKOpenIdConnectProvider")
        assert not t.find_resources("AWS::IAM::OIDCProvider")
    templates["Shared"].resource_count_is("AWS::RDS::DBInstance", 0)
    templates["Shared"].resource_count_is("AWS::EC2::SecurityGroup", 0)


@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_task_definitions_are_arm64_with_main_container(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    t = templates[f"App-{env_name}"]
    task_defs = t.find_resources("AWS::ECS::TaskDefinition")
    assert len(task_defs) >= 2
    for props in (r["Properties"] for r in task_defs.values()):
        assert props["RuntimePlatform"]["CpuArchitecture"] == "ARM64"
        (container,) = props["ContainerDefinitions"]
        assert container["Name"] == "Main"
        assert {s["Name"] for s in container["Secrets"]} >= {
            "SECRET_KEY",
            "DATABASE_URL",
        }
        assert {e["Name"] for e in container["Environment"]} >= {
            "ENVIRONMENT",
            "SENTRY_RELEASE",
        }


@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_express_service_uses_custom_task_definition(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    t = templates[f"App-{env_name}"]
    (express,) = t.find_resources("AWS::ECS::ExpressGatewayService").values()
    props = express["Properties"]
    assert "TaskDefinitionArn" in props
    assert "PrimaryContainer" not in props
    assert props["HealthCheckPath"] == "/readyz"
    assert props["ScalingTarget"]["MinTaskCount"] == 1
    assert len(props["NetworkConfiguration"]["Subnets"]) == len(APP.public_subnets)
    web = [
        r
        for r in t.find_resources("AWS::ECS::TaskDefinition").values()
        if r["Properties"]["Family"].endswith("-web")
    ]
    (port,) = web[0]["Properties"]["ContainerDefinitions"][0]["PortMappings"]
    assert port["ContainerPort"] == 8000
    assert port["Name"]


@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_worker_never_drains_to_zero_on_rollout(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    t = templates[f"App-{env_name}"]
    t.has_resource_properties(
        "AWS::ECS::Service",
        {
            "DesiredCount": ENVIRONMENTS[env_name].worker_count,
            "DeploymentConfiguration": assertions.Match.object_like(
                {"MinimumHealthyPercent": 100, "MaximumPercent": 200}
            ),
        },
    )


@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_worker_capacity_provider_matches_config(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    cfg = ENVIRONMENTS[env_name]  # type: ignore[index]
    t = templates[f"App-{env_name}"]
    t.has_resource_properties(
        "AWS::ECS::Service",
        {
            "ServiceName": f"{APP.name}-{env_name}-worker",
            "CapacityProviderStrategy": [
                {
                    "CapacityProvider": "FARGATE_SPOT"
                    if cfg.worker_spot
                    else "FARGATE",
                    "Weight": 1,
                }
            ],
            "EnableExecuteCommand": True,
        },
    )


@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_no_scheduler_resources(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    """Recurring commands are run by hand until scheduling is decided
    (TODO scheduling-decision) - nothing in the stack starts tasks on a timer."""
    t = templates[f"App-{env_name}"]
    assert not t.find_resources("AWS::Scheduler::Schedule")
    assert not t.find_resources("AWS::Scheduler::ScheduleGroup")


def test_production_database_is_its_own_protected_stack(
    templates: dict[str, assertions.Template],
) -> None:
    db = templates["Db-production"]
    db.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "Engine": "postgres",
            "EngineVersion": "18.4",
            "DeletionProtection": True,
            "BackupRetentionPeriod": 7,
            "DBInstanceClass": "db.t4g.micro",
            "VPCSecurityGroups": [APP.db_security_group_id],
        },
    )
    db.has_resource("AWS::RDS::DBInstance", {"DeletionPolicy": "Retain"})
    # The retained instance is useless without its password: both the
    # generated master secret and the composed DATABASE_URL outlive the stack.
    secrets = db.find_resources("AWS::SecretsManager::Secret")
    assert len(secrets) == 2
    for r in secrets.values():
        assert r["DeletionPolicy"] == "Retain", r
    app = templates["App-production"]
    app.resource_count_is("AWS::RDS::DBInstance", 0)
    app.resource_count_is("AWS::CertificateManager::Certificate", 1)
    app.resource_count_is("AWS::Route53::RecordSet", 1)


def test_deploy_role_is_scoped_to_this_app(
    templates: dict[str, assertions.Template],
) -> None:
    """The GitHub role must not be able to roll or run tasks on another app."""
    t = templates["Shared"]
    policies = t.find_resources("AWS::IAM::Policy")
    statements = [
        s
        for p in policies.values()
        for s in p["Properties"]["PolicyDocument"]["Statement"]
    ]
    by_action: dict[str, list[dict[str, object]]] = {}
    for s in statements:
        for action in s["Action"] if isinstance(s["Action"], list) else [s["Action"]]:
            by_action.setdefault(action, []).append(s)
    for action in ("ecs:RunTask", "ecs:UpdateService", "ecs:DescribeServices"):
        (statement,) = by_action[action]
        assert "ecs:cluster" in statement["Condition"]["ArnEquals"], action  # type: ignore[index]
    (tag,) = by_action["ecs:TagResource"]
    assert "*" not in tag["Resource"]  # type: ignore[operator]
    (pass_role,) = by_action["iam:PassRole"]
    resources = pass_role["Resource"]
    assert isinstance(resources, list)
    patterns = [r for r in resources if isinstance(r, str)]
    assert patterns == [f"arn:aws:iam::{APP.account}:role/{APP.name}-App-*"]
    assert not [a for a in by_action if a.startswith("scheduler:")]
    (logs_stmt,) = by_action["logs:GetLogEvents"]
    assert all(
        f":log-group:/aws/ecs/{APP.name}-" in r
        for r in logs_stmt["Resource"]  # type: ignore[union-attr]
    )
    assert "*" not in logs_stmt["Resource"]  # type: ignore[operator]


def test_dev_uses_shared_database(templates: dict[str, assertions.Template]) -> None:
    assert "Db-dev" not in templates
    templates["App-dev"].resource_count_is("AWS::RDS::DBInstance", 0)


@pytest.mark.parametrize("env_name", list(ENVIRONMENTS))
def test_logs_and_bucket_hardening(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    t = templates[f"App-{env_name}"]
    t.has_resource_properties("AWS::Logs::LogGroup", {"RetentionInDays": 30})
    t.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": {
                "BlockPublicAcls": True,
                "RestrictPublicBuckets": True,
            }
        },
    )


def test_worker_task_has_a_liveness_probe(
    templates: dict[str, assertions.Template],
) -> None:
    """The worker has no port for the ALB: ECS itself must notice a task
    that can no longer reach the database."""
    t = templates["App-dev"]
    task_defs = t.find_resources("AWS::ECS::TaskDefinition")
    by_family = {
        r["Properties"]["Family"]: r["Properties"]["ContainerDefinitions"][0]
        for r in task_defs.values()
    }
    worker = by_family[f"{APP.name}-dev-worker"]
    assert "ensure_connection" in " ".join(worker["HealthCheck"]["Command"])
    assert "HealthCheck" not in by_family[f"{APP.name}-dev-web"]


def test_listener_rule_edit_is_scoped_to_the_rule(
    templates: dict[str, assertions.Template],
) -> None:
    """The Express listener is shared by every app in the account: the
    custom resource that adds our host header may edit only our rule."""
    t = templates["App-production"]  # the env with a custom domain
    statements = [
        s
        for p in t.find_resources("AWS::IAM::Policy").values()
        for s in p["Properties"]["PolicyDocument"]["Statement"]
        if "elasticloadbalancing:ModifyRule" in s["Action"]
    ]
    assert statements, "no ModifyRule statement"
    for s in statements:
        assert s["Resource"] != "*", s
