"""Deployment trigger: run the release task and wait for it to exit 0.

Invoked by CDK (aws_cdk.triggers) before the web/worker services are
created or updated, so a brand-new environment is migrated before the first
task ever answers /readyz. CD runs the same command on every deploy; both
are idempotent.
"""

import os
import time

import boto3

ecs = boto3.client("ecs")

# The Lambda itself is capped at 15 minutes; stop polling with enough margin
# to raise a readable error instead of a bare timeout. A migration that needs
# longer is run by hand first (`just infra-run-task`), then the deploy re-run.
DEADLINE_SECONDS = 13 * 60


def handler(event, context):
    cluster = os.environ["CLUSTER"]
    started = ecs.run_task(
        cluster=cluster,
        taskDefinition=os.environ["TASK_DEFINITION"],
        launchType="FARGATE",
        networkConfiguration={
            "awsvpcConfiguration": {
                "subnets": os.environ["SUBNETS"].split(","),
                "securityGroups": [os.environ["SECURITY_GROUP"]],
                "assignPublicIp": "ENABLED",
            }
        },
        overrides={
            "containerOverrides": [
                {"name": "Main", "command": ["sh", "-c", os.environ["COMMAND"]]}
            ]
        },
    )
    if not started["tasks"]:
        # Capacity / configuration problems come back here, not as exceptions.
        msg = f"release task did not launch: {started.get('failures')}"
        raise RuntimeError(msg)
    task = started["tasks"][0]["taskArn"]
    deadline = time.monotonic() + DEADLINE_SECONDS
    while True:
        time.sleep(10)
        described = ecs.describe_tasks(cluster=cluster, tasks=[task])["tasks"][0]
        if described["lastStatus"] == "STOPPED":
            break
        if time.monotonic() > deadline:
            msg = f"release task {task} still running after {DEADLINE_SECONDS}s"
            raise RuntimeError(msg)
    code = described["containers"][0].get("exitCode")
    if code != 0:
        msg = (
            f"release task {task} exited with {code}: {described.get('stoppedReason')}"
        )
        raise RuntimeError(msg)
    return {"task": task}
