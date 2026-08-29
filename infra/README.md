# infra/ — AWS CDK (Python)

Infrastructure for the backend: ECS Express Mode (web), Fargate worker, RDS
PostgreSQL 18, S3 + CloudFront, EventBridge Scheduler. The runbook is
[`docs/DEPLOYMENT.md`](../docs/DEPLOYMENT.md); this folder is the code.

```
just infra-install            # uv sync + npm ci (CDK CLI is pinned in package.json)
just infra-synth              # synth every stack (no AWS credentials needed)
just infra-test               # template assertions
just infra-diff dev           # what a deploy would change
just infra-deploy-shared      # once per app
just infra-deploy dev         # per environment (reads the live image tag)
```

Topology and env-var ownership live in `backend_infra/config.py`; resource
and stack names in `backend_infra/naming.py`. Stacks, all prefixed with the
app name because many apps share one account: `<app>-Shared` (ECR, cluster,
roles), `<app>-Db-<env>` (dedicated RDS, stateful) and `<app>-App-<env>`
(everything else, stateless). Account-level resources (GitHub OIDC provider,
shared dev RDS, DB security group) are referenced by value in `AppConfig`,
never created here.
