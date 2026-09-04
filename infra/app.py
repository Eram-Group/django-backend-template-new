"""CDK entry point (``cdk.json`` runs this). The stack graph itself is
``backend_infra.synth.build_app`` - shared with the synth tests.

``cdk deploy <app>-App-<env>`` deploys its dependencies (``<app>-Shared``,
``<app>-Db-<env>``) first.

Context (``-c key=value``):
  image_tag       ECR tag baked into the task definitions. REQUIRED - there is
                  no default, so a deploy can never register task definitions
                  pointing at a tag that does not exist. ``just infra-deploy``
                  resolves the live one; ``just infra-synth`` passes ``synth``.
  sentry_release  value of SENTRY_RELEASE (defaults to image_tag)
"""

from aws_cdk import App
from backend_infra.synth import build_app

image_tag = App().node.try_get_context("image_tag")
if not image_tag:
    msg = "cdk: pass -c image_tag=<git sha in ECR> (image_tag=synth for synth-only)"
    raise SystemExit(msg)
sentry_release = App().node.try_get_context("sentry_release") or image_tag

build_app(image_tag=str(image_tag), sentry_release=str(sentry_release)).synth()
