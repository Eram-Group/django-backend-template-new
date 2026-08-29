"""Synth every stack and assert the load-bearing properties."""

import aws_cdk as cdk
import pytest
from aws_cdk import assertions
from backend_infra import naming
from backend_infra.config import APP
from backend_infra.config import ENVIRONMENTS
from backend_infra.config import SCHEDULES
from backend_infra.stacks.app_env import AppEnvStack
from backend_infra.stacks.database import DatabaseStack
from backend_infra.stacks.shared import SharedStack


@pytest.fixture(scope="module")
def templates() -> dict[str, assertions.Template]:
    app = cdk.App()
    env = cdk.Environment(account=APP.account, region=APP.region)
    shared = SharedStack(app, naming.shared_stack(APP), app=APP, env=env)
    stacks = {"Shared": shared}
    for name, cfg in ENVIRONMENTS.items():
        database = None
        if cfg.database == "dedicated":
            stacks[f"Db-{name}"] = DatabaseStack(
                app, naming.db_stack(APP, name), app=APP, env_config=cfg, env=env
            )
            database = stacks[f"Db-{name}"].database  # type: ignore[attr-defined]
        stacks[f"App-{name}"] = AppEnvStack(
            app,
            naming.app_stack(APP, name),
            app=APP,
            env_config=cfg,
            shared=shared,
            database=database,
            image_tag="synth",
            sentry_release="synth",
            env=env,
        )
    return {
        name: assertions.Template.from_stack(stack) for name, stack in stacks.items()
    }


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
def test_schedules_cover_every_job(
    templates: dict[str, assertions.Template], env_name: str
) -> None:
    t = templates[f"App-{env_name}"]
    schedules = t.find_resources("AWS::Scheduler::Schedule")
    assert len(schedules) == len(SCHEDULES)
    for r in schedules.values():
        assert r["Properties"]["State"] == "ENABLED"
        assert r["Properties"]["FlexibleTimeWindow"] == {"Mode": "OFF"}
        assert '"name":"Main"' in r["Properties"]["Target"]["Input"]


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


def test_stack_names_lead_with_the_app_name() -> None:
    """Many apps share one account+region; bare Shared/App-<env> would collide."""
    assert naming.shared_stack(APP) == f"{APP.name}-Shared"
    assert naming.db_stack(APP, "production") == f"{APP.name}-Db-production"
    assert naming.app_stack(APP, "dev") == f"{APP.name}-App-dev"
    app = cdk.App()
    stack = SharedStack(app, naming.shared_stack(APP), app=APP)
    assert stack.stack_name == f"{APP.name}-Shared"


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
    (pass_role,) = by_action["iam:PassRole"]
    resources = pass_role["Resource"]
    assert isinstance(resources, list)
    patterns = [r for r in resources if isinstance(r, str)]
    assert patterns == [f"arn:aws:iam::{APP.account}:role/{APP.name}-App-*"]
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
