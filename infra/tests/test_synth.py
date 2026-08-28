"""Synth every stack and assert the load-bearing properties."""

import aws_cdk as cdk
import pytest
from aws_cdk import assertions
from backend_infra.config import APP
from backend_infra.config import ENVIRONMENTS
from backend_infra.config import SCHEDULES
from backend_infra.stacks.app_env import AppEnvStack
from backend_infra.stacks.shared import SharedStack


@pytest.fixture(scope="module")
def templates() -> dict[str, assertions.Template]:
    app = cdk.App()
    env = cdk.Environment(account=APP.account, region=APP.region)
    shared = SharedStack(app, "Shared", app=APP, env=env)
    stacks = {"Shared": shared}
    for name, cfg in ENVIRONMENTS.items():
        stacks[f"App-{name}"] = AppEnvStack(
            app,
            f"App-{name}",
            app=APP,
            env_config=cfg,
            shared=shared,
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
    t.has_resource_properties(
        "AWS::RDS::DBInstance", {"Engine": "postgres", "EngineVersion": "18.4"}
    )
    t.has_resource_properties(
        "AWS::ECS::TaskDefinition",
        {"Family": f"{APP.shared_dev_db_identifier}-bootstrap"},
    )


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
    states = {
        r["Properties"]["Name"]: r["Properties"]["State"] for r in schedules.values()
    }
    assert states[f"{APP.name}-{env_name}-sample-scheduled-job"] == "DISABLED"
    for r in schedules.values():
        assert r["Properties"]["FlexibleTimeWindow"] == {"Mode": "OFF"}
        assert '"name":"Main"' in r["Properties"]["Target"]["Input"]


def test_production_has_dedicated_protected_database(
    templates: dict[str, assertions.Template],
) -> None:
    t = templates["App-production"]
    t.has_resource_properties(
        "AWS::RDS::DBInstance",
        {
            "DeletionProtection": True,
            "BackupRetentionPeriod": 7,
            "DBInstanceClass": "db.t4g.micro",
        },
    )
    t.resource_count_is("AWS::CertificateManager::Certificate", 1)
    t.resource_count_is("AWS::Route53::RecordSet", 1)


def test_dev_uses_shared_database(templates: dict[str, assertions.Template]) -> None:
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
