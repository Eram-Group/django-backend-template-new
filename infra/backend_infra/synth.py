"""Assemble the CDK app - the one place the stack graph is built.

``app.py`` (the CDK entry point) and ``tests/test_synth.py`` both call
``build_app`` so the assertions run against exactly what deploys, including
the termination-protection flags.
"""

import json
from pathlib import Path

from aws_cdk import App
from aws_cdk import Environment
from aws_cdk import Tags

from backend_infra import naming
from backend_infra.config import APP
from backend_infra.config import ENVIRONMENTS
from backend_infra.stacks.app_env import AppEnvStack
from backend_infra.stacks.database import DatabaseStack
from backend_infra.stacks.shared import SharedStack

CDK_JSON = Path(__file__).resolve().parents[1] / "cdk.json"


def _feature_flags() -> dict[str, object]:
    """The ``context`` block of cdk.json. The CDK CLI feeds it to the app;
    an in-process ``App()`` (the tests) would otherwise synthesize under the
    legacy defaults and assert against templates that never deploy."""
    context: dict[str, object] = json.loads(CDK_JSON.read_text())["context"]
    return context


def build_app(*, image_tag: str, sentry_release: str) -> App:
    """``<app>-Shared`` once per app, ``<app>-Db-<env>`` + ``<app>-App-<env>``
    per env (names in ``backend_infra.naming``). ``<app>-Db-<env>`` exists
    only for environments with a dedicated database."""
    app = App(context=_feature_flags())
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
    return app
