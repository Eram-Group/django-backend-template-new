"""IAM roles: task execution, task, Express infrastructure, GitHub deploy."""

from aws_cdk import aws_iam as iam
from constructs import Construct


def task_execution_role(scope: Construct, construct_id: str) -> iam.Role:
    """Pulls the image, writes logs, reads the ``secrets`` entries."""
    return iam.Role(
        scope,
        construct_id,
        assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        managed_policies=[
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonECSTaskExecutionRolePolicy"
            )
        ],
    )


def task_role(
    scope: Construct,
    construct_id: str,
    *,
    ses_identity_arn: str | None,
    from_domain: str | None,
) -> iam.Role:
    """What the application code may call: S3 (granted by the bucket) + SES."""
    role = iam.Role(
        scope, construct_id, assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com")
    )
    if ses_identity_arn:
        conditions = (
            {"StringLike": {"ses:FromAddress": f"*@{from_domain}"}}
            if from_domain
            else None
        )
        role.add_to_policy(
            iam.PolicyStatement(
                actions=["ses:SendEmail", "ses:SendRawEmail"],
                resources=[ses_identity_arn],
                conditions=conditions,
            )
        )
    return role


def express_infrastructure_role(scope: Construct, construct_id: str) -> iam.Role:
    """Lets Express Mode create/manage the ALB, target groups, SGs, scaling."""
    return iam.Role(
        scope,
        construct_id,
        assumed_by=iam.ServicePrincipal("ecs.amazonaws.com"),
        managed_policies=[
            iam.ManagedPolicy.from_aws_managed_policy_name(
                "service-role/AmazonECSInfrastructureRoleforExpressGatewayServices"
            )
        ],
    )


def github_deploy_role(
    scope: Construct,
    construct_id: str,
    *,
    role_name: str,
    provider_arn: str,
    github_repo: str,
    ecr_repository_arn: str,
    cluster_arn: str,
    log_group_arns: list[str],
    passable_role_arns: list[str],
) -> iam.Role:
    """Assumed by the deploy workflows via OIDC - no static AWS keys.

    Many apps share one AWS account, so every statement is scoped to THIS
    app: its ECR repository, its cluster (``ecs:cluster`` condition), its log
    groups and its own task/execution roles. The role must never be able to
    run a task or roll a service on another app's cluster.
    """
    role = iam.Role(
        scope,
        construct_id,
        role_name=role_name,
        assumed_by=iam.WebIdentityPrincipal(
            provider_arn,
            conditions={
                "StringEquals": {
                    "token.actions.githubusercontent.com:aud": "sts.amazonaws.com"
                },
                # GitHub may issue the subject with immutable ids appended
                # (repo:Owner@123/name@456:...); accept both spellings.
                "StringLike": {
                    "token.actions.githubusercontent.com:sub": [
                        f"repo:{github_repo}:*",
                        "repo:{}@*/{}@*:*".format(*github_repo.split("/", 1)),
                    ]
                },
            },
        ),
        description=f"GitHub Actions deploy role for {github_repo}",
    )
    role.add_to_policy(
        iam.PolicyStatement(actions=["ecr:GetAuthorizationToken"], resources=["*"])
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "ecr:BatchCheckLayerAvailability",
                "ecr:BatchGetImage",
                "ecr:GetDownloadUrlForLayer",
                "ecr:DescribeImages",
                "ecr:InitiateLayerUpload",
                "ecr:UploadLayerPart",
                "ecr:CompleteLayerUpload",
                "ecr:PutImage",
            ],
            resources=[ecr_repository_arn],
        )
    )
    # Task definitions have no cluster and no useful resource ARN before they
    # exist; RunTask/UpdateService with them is what the cluster condition and
    # the PassRole allowlist below constrain.
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "ecs:DescribeTaskDefinition",
                "ecs:RegisterTaskDefinition",
                "ecs:TagResource",
            ],
            resources=["*"],
        )
    )
    # Service rollouts and one-off tasks: this app's cluster only.
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "ecs:DescribeServices",
                "ecs:UpdateService",
                "ecs:RunTask",
                "ecs:DescribeTasks",
                "ecs:ListTasks",
            ],
            resources=["*"],
            conditions={"ArnEquals": {"ecs:cluster": cluster_arn}},
        )
    )
    # Express Mode services: the service ARN shape and the condition keys the
    # Express APIs honour are not in the Service Authorization Reference yet
    # (2026-08-29), so these two calls stay unscoped. Re-scope by service ARN
    # the moment AWS documents it.
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "ecs:DescribeExpressGatewayService",
                "ecs:UpdateExpressGatewayService",
            ],
            resources=["*"],
        )
    )
    role.add_to_policy(
        iam.PolicyStatement(
            actions=["iam:PassRole"],
            resources=passable_role_arns,
            conditions={
                "StringEquals": {
                    "iam:PassedToService": [
                        "ecs-tasks.amazonaws.com",
                        "ecs.amazonaws.com",
                    ]
                }
            },
        )
    )
    # Failure-path diagnostics in the deploy workflow: this app's log groups.
    role.add_to_policy(
        iam.PolicyStatement(
            actions=[
                "logs:GetLogEvents",
                "logs:FilterLogEvents",
                "logs:DescribeLogStreams",
            ],
            resources=[*log_group_arns, *(f"{arn}:*" for arn in log_group_arns)],
        )
    )
    return role
