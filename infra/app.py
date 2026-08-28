"""CDK entry point: ``Shared`` once per app, ``Db-<env>`` + ``App-<env>`` per env.

``cdk deploy App-<env>`` deploys its dependencies (``Shared``, ``Db-<env>``)
first; ``Db-<env>`` exists only for environments with a dedicated database.

Context (``-c key=value``):
  image_tag       ECR tag baked into the task definitions (required to deploy;
                  ``just infra-deploy`` resolves the live one automatically)
  sentry_release  value of SENTRY_RELEASE (defaults to image_tag)
  nag=true        run cdk-nag AwsSolutions checks
"""

from aws_cdk import App
from aws_cdk import Aspects
from aws_cdk import Environment
from aws_cdk import Tags
from backend_infra.config import APP
from backend_infra.config import ENVIRONMENTS
from backend_infra.stacks.app_env import AppEnvStack
from backend_infra.stacks.database import DatabaseStack
from backend_infra.stacks.shared import SharedStack

app = App()
image_tag = str(app.node.try_get_context("image_tag") or "synth")
sentry_release = str(app.node.try_get_context("sentry_release") or image_tag)
aws_env = Environment(account=APP.account, region=APP.region)

shared = SharedStack(app, "Shared", app=APP, env=aws_env, termination_protection=True)
for name, cfg in ENVIRONMENTS.items():
    database = None
    if cfg.database == "dedicated":
        database = DatabaseStack(
            app,
            f"Db-{name}",
            app=APP,
            env_config=cfg,
            env=aws_env,
            termination_protection=True,
        ).database
    AppEnvStack(
        app,
        f"App-{name}",
        app=APP,
        env_config=cfg,
        shared=shared,
        database=database,
        image_tag=image_tag,
        sentry_release=sentry_release,
        env=aws_env,
        termination_protection=cfg.name == "production",
    )

Tags.of(app).add("app", APP.name)
Tags.of(app).add("managed-by", "cdk")

if app.node.try_get_context("nag"):
    from cdk_nag import AwsSolutionsChecks

    Aspects.of(app).add(AwsSolutionsChecks(verbose=True))

app.synth()
