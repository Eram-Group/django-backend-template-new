"""CDK entry point: ``<app>-Shared`` once per app, ``<app>-Db-<env>`` +
``<app>-App-<env>`` per env (names in ``backend_infra.naming``).

``cdk deploy <app>-App-<env>`` deploys its dependencies (``<app>-Shared``,
``<app>-Db-<env>``) first; ``<app>-Db-<env>`` exists only for environments
with a dedicated database.

Context (``-c key=value``):
  image_tag       ECR tag baked into the task definitions (required to deploy;
                  ``just infra-deploy`` resolves the live one automatically)
  sentry_release  value of SENTRY_RELEASE (defaults to image_tag)
"""

from aws_cdk import App
from aws_cdk import Environment
from aws_cdk import Tags
from backend_infra import naming
from backend_infra.config import APP
from backend_infra.config import ENVIRONMENTS
from backend_infra.stacks.app_env import AppEnvStack
from backend_infra.stacks.database import DatabaseStack
from backend_infra.stacks.shared import SharedStack

app = App()
image_tag = str(app.node.try_get_context("image_tag") or "synth")
sentry_release = str(app.node.try_get_context("sentry_release") or image_tag)
aws_env = Environment(account=APP.account, region=APP.region)

shared = SharedStack(
    app, naming.shared_stack(APP), app=APP, env=aws_env, termination_protection=True
)
for name, cfg in ENVIRONMENTS.items():
    database = None
    if cfg.database == "dedicated":
        database = DatabaseStack(
            app,
            naming.db_stack(APP, name),
            app=APP,
            env_config=cfg,
            env=aws_env,
            termination_protection=True,
        ).database
    AppEnvStack(
        app,
        naming.app_stack(APP, name),
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

app.synth()
