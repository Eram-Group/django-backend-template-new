# Deployment (AWS)

How to stand the backend up on AWS once, what it costs, and how every deploy
flows. The reasoning behind the design is in
[AWS_ARCHITECTURE.md](AWS_ARCHITECTURE.md); the infrastructure is code in
[`infra/`](../infra) (AWS CDK, Python); the pipelines are
`.github/workflows/deploy-*.yml`.

## Architecture

```
GitHub Actions (OIDC, arm64 build) ──push :sha──▶ ECR eram/<app>
        │
        ├─ 1. release  : ECS RunTask (worker family)  manage.py release (check --deploy, migrate, createcachetable, collectstatic)
        ├─ 2. web      : update ECS Express Mode service   canary, auto-rollback on 5XX / unhealthy
        └─ 3. worker   : update ECS Fargate service        db_worker --queue-name default,bulk

clients ──HTTPS──▶ ALB (Express-managed, shared by every Express service in the VPC)
                     └─▶ [web] Fargate ARM64 · gunicorn · CPU target tracking, 1..N tasks
                                  │
CloudFront (OAC) ──▶ S3 static/ media/         ├──▶ RDS PostgreSQL 18 (DB + cache table + sessions + task queue)
                                              └──▶ SES
[worker] Fargate ARM64 · db_worker · Fargate Spot in every environment
```

| Concern | Service | Notes |
|---|---|---|
| Web | **ECS Express Mode** on Fargate (ARM64) | Express provisions and operates the ALB, HTTPS listener + certificate, target groups, security groups, auto scaling and canary deployments. We hand it a CDK-owned task definition (container **`Main`**, port 8000, health path `/readyz`). URL: `https://<service>-<hash>.ecs.eu-central-1.on.aws`. |
| Worker | plain ECS Fargate service, same cluster | `manage.py db_worker --queue-name default,bulk`, 0.25 vCPU / 0.5 GB, `stopTimeout` 120 s (≥ the longest task), ECS Exec enabled. **Fargate Spot in every environment** (≈ 70 % off): jobs are idempotent and enqueued inside the writing transaction, and `db_worker` finishes the current job on SIGTERM, so a reclaim delays work by minutes and never loses it. The release task stays on-demand. Flip `EnvConfig.worker_spot` only for a worker running long, non-restartable jobs. |
| Release step | one-off `RunTask` on the worker family, on-demand | Runs before every web rollout from the exact revision being deployed. |
| Recovery | **by hand, from the admin** | Nothing runs on a timer. Stuck payments and deliveries are counted on the sidebar and listed under "Needs attention"; every known-risk moment logs an ERROR (Sentry). See *Recovery* below. |
| Database | **RDS for PostgreSQL 18** | Production: `production-<app>` db.t4g.micro, gp3 20 GB (autoscaling to 100), single-AZ, 7-day backups, deletion protection. Dev/staging: one shared `development-shared-pg18` db.t4g.small for every app's non-prod databases. Postgres also serves as cache, session store and task queue — there is no Redis. |
| Files | S3 (private, SSE-S3) + **CloudFront** (OAC) | `static/` + `media/` prefixes (django-storages). |
| Email | SES, region-local identity | Task role may `ses:SendEmail` from the identity's domain only. |
| Config | ECS `environment` + `secrets` | Non-secret values in the task definition (from `config.py`), secrets from one Secrets Manager JSON secret `<env>/<app>`; production `DATABASE_URL` is composed by CDK into `<env>/<app>/database-url`. |
| Network | default VPC, public subnets in all three AZs, public task IPs | No NAT gateway. The DB security group allows 5432 from the VPC CIDR. |
| Observability | CloudWatch Logs (30-day retention), Sentry | JSON structlog with `request_id`; Express adds a 5XX/unhealthy alarm per service. |
| CI identity | GitHub OIDC provider + `<app>-github-deploy` role | No static AWS keys anywhere. |

**Express Mode facts that shape the design** — the primary container must be
named `Main` with exactly one named TCP port mapping; `taskDefinitionArn` is
mutually exclusive with the "primary container" shortcut (roles, cpu, memory
are read from the task definition); deployments are always canary (not
configurable); the first Express service in a VPC pins the shared ALB's
subnets, so every service passes subnets from all AZs; a custom domain is not
first-class (see below); one ALB is shared by up to 25 Express services in the
VPC — dev **and** production of every app, which is cheap but couples them (a
dedicated production VPC is the escape hatch).

### Environments

`dev` and `production` are defined; `staging` is supported by the app
(`ENVIRONMENT=staging`, test payment keys) and is one more `EnvConfig` entry.
Stacks, per app and prefixed with the app name because every app shares one
account+region: `<app>-Shared` (ECR, cluster, roles), `<app>-Db-<env>` (the
dedicated RDS instance of environments with `database="dedicated"`, kept
apart so the app stack can be recreated without touching data) and
`<app>-App-<env>` (everything else, stateless). Resources that
exist **once per AWS account** — the GitHub OIDC provider, the shared dev RDS
instance `development-shared-pg18` and the database security group — are
never created by any app's stacks: `AppConfig` references them by value, so
the ten apps copied from this template share them without one repo owning
them (see *Account prerequisites*).

### Who owns what

- **CDK owns** every resource, including the task definitions — created from
  the image tag passed as `-c image_tag=<sha>`.
- **CD mutates** task definitions (new revisions with the new image and
  `SENTRY_RELEASE`) and repoints the services. CloudFormation ignores that
  drift; it only re-registers when the *template* changes.
- `just infra-deploy <env>` reads the tag the worker SERVICE is running
  (`infra/scripts/live_context.sh`; not the family's latest ACTIVE revision,
  which a failed release can leave behind) before running `cdk deploy`, so a
  config-only deploy never reverts the image. The first deploy of an
  environment is `just infra-deploy-first <env> <sha>`.

## Cost (eu-central-1, on-demand list prices, 730 h/month)

| Item | dev | production |
|---|---|---|
| Web task, Fargate ARM (dev 0.25 vCPU / 1 GB, prod 0.5 vCPU / 1 GB; 1-task floor) | $9.78 | $16.58 |
| Worker task, Fargate ARM 0.25 vCPU / 0.5 GB, Spot (≈ −70 % vs $8.29 on-demand) | ≈ $2.50 | ≈ $2.50 |
| Public IPv4 per task ENI ($0.005/h) — the price of "no NAT" | $7.30 | $7.30 |
| RDS PostgreSQL 18 | — (shared instance) | t4g.micro $13.87 + gp3 20 GB $2.74 |
| Secrets Manager | $0.40 | $1.20 |
| CloudFront / S3 / SES / CloudWatch Logs | ≈ $2 | ≈ $3–5 |
| **Environment subtotal** | **≈ $23** | **≈ $49** |

Shared pools, paid once per account: ALB ≈ $34/mo ($19.71 + LCU + 3 public
IPs) split across every Express service in the VPC; `development-shared-pg18`
≈ $30/mo split across every dev/staging database. With ~5 apps that lands at
≈ $33/mo per dev environment and ≈ $52/mo per production environment.

Compared with the previous shape (App Runner 1 vCPU/2 GB per env + x86
on-demand workers + a NAT gateway for App Runner's VPC egress, ≈ $105–120/mo
per app with dev + prod), this lands at ≈ $85/mo per app at low traffic: the
NAT share and x86 workers go away, public IPv4 ($3.65/task) and the ALB share
come in. The gap widens under real traffic (App Runner bills per active
vCPU-hour; Fargate is flat and only scales out above 60 % CPU) and by another
≈ $38/mo account-wide once the NAT gateway can be deleted. App Runner is
closed to new customers since 2026-04-30; AWS names Express Mode as its
replacement.

### Scale-to-zero, honestly

The web tier cannot scale to zero on Fargate (the ALB needs ≥ 1 healthy
target); its floor is one 0.25 vCPU task ≈ $10/mo. The worker is the only
tier that could: replace the service with a scheduled `db_worker --batch`
run if minutes of latency are acceptable (not done - Spot already makes it
≈ $2.5/mo). Lambda was
rejected for the web tier: one request per sandbox means a database
connection per concurrent request (a t4g.micro would collapse), plus cold
starts and a second runtime to maintain.

## Account prerequisites (once per AWS account, not per app)

Already in place in the Eram account; every app's `AppConfig` points at
them. For a new account create them once, then copy the values into
`config.py`:

| Resource | `AppConfig` field | How |
|---|---|---|
| GitHub OIDC provider | `github_oidc_provider_arn` | `aws iam create-open-id-connect-provider --url https://token.actions.githubusercontent.com --client-id-list sts.amazonaws.com` |
| DB security group (5432 from the VPC CIDR) | `db_security_group_id` | `aws ec2 create-security-group --group-name postgres-from-vpc --description "Postgres from the VPC" --vpc-id <vpc>` + `authorize-security-group-ingress --protocol tcp --port 5432 --cidr <vpc-cidr>` |
| Shared dev RDS (PostgreSQL 18, db.t4g.small, gp3 20 GB, RDS-managed master password, reachable from the team's IPs so databases are created with `psql`) | — (apps only see their `DATABASE_URL`) | `aws rds create-db-instance --db-instance-identifier development-shared-pg18 --engine postgres --engine-version 18 --db-instance-class db.t4g.small --allocated-storage 20 --storage-type gp3 --storage-encrypted --master-username postgres --manage-master-user-password --publicly-accessible --vpc-security-group-ids <sg> --backup-retention-period 1 --deletion-protection`, then `aws ec2 authorize-security-group-ingress --group-id <sg> --protocol tcp --port 5432 --cidr <your-ip>/32` per team member. (Existing private instance: `aws rds modify-db-instance --db-instance-identifier development-shared-pg18 --publicly-accessible --apply-immediately`.) TLS is required (`sslmode=require`); the master password lives in Secrets Manager and is read in the console when needed — never printed by tooling. |

### Tagging convention (every resource, every app)

Three tags, lowercase, on everything that can carry one - CDK applies them to
its own stacks (`synth.py` / `app_env.py`), hand-made resources get them by
hand at creation:

| Key | Values | Meaning |
|---|---|---|
| `app` | the app name (`weem`, `dars`, `1k`, ...); `shared` for account-level resources | which product pays for it |
| `env` | `dev` \| `staging` \| `production` \| `shared` | which environment; `shared` for account-level resources (NAT, bastion, hosted zone, CloudTrail bucket, VPC connector, DB security group) |
| `managed-by` | `cdk` \| `manual` | whether a stack owns it |

`app` and `env` are the activated cost-allocation tags: Cost Explorer can
group by them, so a resource without both is invisible in the per-app view.
Never use `App`/`Env`/`prod` - the account was unified to this scheme on
2026-09-05 (the legacy keys were removed), and Cost Explorer only knows the
lowercase keys. Resources shared across environments but owned by one app
(the ECR repository, the ECS cluster) carry `app` without `env`.

Why not CDK: an account-global resource in a per-app stack has exactly one
owner, and a template copied into ten repos cannot express that safely —
one `cdk destroy` or a flag flipped in two repos would take every dev
database down.

## One-time bootstrap

Prerequisites: `aws login` with an admin profile, Node 22 (`npx cdk`), `jq`.

1. `just infra-install` — Python deps + the pinned CDK CLI.
2. `cd infra && npx cdk bootstrap aws://<account>/eu-central-1` (once per
   account/region).
3. `infra/backend_infra/config.py` already carries `APP.name` and
   `APP.github_repo` (set per project - docs/NEW_PROJECT.md; the
   account-level fields stay as they are for every app in the same
   account); edit `ENVIRONMENTS`:
   sizes, domain, frontend origins.
4. `just infra-deploy-shared` — ECR, cluster, roles (stack `<app>-Shared`).
   If the ECR repository `eram/<app>` already exists, import it first
   (`npx cdk import <app>-Shared`) or delete it.
5. Create the app secret with every key present (empty = unset):
   `aws secretsmanager create-secret --name dev/<app> --secret-string "$(just infra-secret-skeleton dev)"`
   then fill the values in the console (`SECRET_KEY`, `ADMIN_URL` - an
   unguessable path such as `manage-3f9a1c/`, trailing slash - `DATABASE_URL`
   for shared-DB envs, gateway keys…). Production gets its `DATABASE_URL` from
   CDK — leave that key out (`infra-secret-skeleton production` already does).
6. Dev database on the shared instance — once per app, with `psql` as the
   master user (`psql "host=<endpoint> user=postgres dbname=postgres sslmode=require"`):
   ```sql
   CREATE ROLE <app>_dev LOGIN PASSWORD '<choose one>';
   GRANT <app>_dev TO postgres;   -- RDS master is not a superuser: needed to hand over ownership
   CREATE DATABASE <app>_dev OWNER <app>_dev;
   \c <app>_dev
   CREATE EXTENSION IF NOT EXISTS postgis;  -- not an RDS trusted extension: only rds_superuser (the master) can create it
   ```
   then set `DATABASE_URL=postgres://<app>_dev:<password>@<endpoint>:5432/<app>_dev`
   in the `dev/<app>` secret.
7. GitHub repo variables: `AWS_ECR_REPOSITORY=eram/<app>`,
   `AWS_OIDC_ROLE_ARN` (`<app>-Shared` output `GithubDeployRoleArn`),
   `AWS_REGION`. Do not push to `main` before step 9 is complete: the
   `deploy` job expects every `ECS_*` / `EXPRESS_*` variable and fails the run
   on a missing one — it does not skip.
8. `just infra-deploy-first dev <sha>` — first environment deploy
   (≈ 10 min; the Express service provisions the ALB). The stack's release
   trigger runs `manage.py release` (`check --deploy`, `migrate`,
   `createcachetable`, `collectstatic`) on the worker task
   definition *before* the services are
   created, so `/readyz` is green on the first task.
9. Create the GitHub environment `dev` and copy the `<app>-App-dev` outputs into
   its variables:
   `ECS_CLUSTER`, `ECS_FAMILY_WEB`, `ECS_FAMILY_WORKER`, `ECS_SERVICE_WORKER`,
   `EXPRESS_SERVICE_ARN`, `EXPRESS_SERVICE_NAME`, `ECS_SUBNETS`,
   `ECS_SECURITY_GROUPS`. Then push to `main` (or re-run `Deploy dev`).
10. `just infra-run-task dev python manage.py createsu` (first superuser) and
    `just infra-run-task dev python manage.py shell -c "1/0"` (Sentry smoke:
    the event must carry `environment=dev` and `release=<sha>`).
    Then, in the admin, Location → Countries → **Load countries** and pick
    the markets (the only way country rows come to exist; flags download in
    the worker afterwards).
11. Production: `just infra-deploy-first production <sha>` deploys
    `<app>-Db-production` first (RDS takes ~10 min; the instance and its
    secrets carry deletion protection and outlive the app stack), then
    `<app>-App-production` (the ACM
    certificate validates automatically through Route 53); fill
    `production/<app>`, create the GitHub environment
    `production` with required reviewers, add its variables, then dispatch
    `Deploy production`.

SES: the account is already out of the sandbox and `eramapps.com` is a
verified identity; `DEFAULT_FROM_EMAIL` must use that domain (or add an
identity and change `AppConfig.ses_identity`).

## Deploy flow

1. **release** — `amazon-ecs-deploy-task-definition` registers a worker
   revision with the new image and runs it once (on-demand Fargate) with the
   command `manage.py release` (`check --deploy --fail-level WARNING`,
   `migrate`, `createcachetable`, `collectstatic`). Any Django deploy warning
   stops the rollout before the
   database is touched. Migrations must be expand/contract: web rolls before
   worker by design. (`cdk deploy` runs the same command through its release
   trigger before touching the services, so IaC-driven rollouts are migrated
   too.)
2. **web** — a web revision is registered, then
   `aws ecs update-express-gateway-service --task-definition-arn …`. Express
   runs a canary; its alarm rolls back automatically on 5XX/unhealthy targets.
   The workflow waits for `services-stable` and then confirms the active
   configuration points at the new revision — a rolled-back deploy fails the
   job loudly.
3. **worker** — plain `UpdateService` with circuit breaker + rollback.

Production promotes the exact dev image by manifest retag (build once), then
runs the same three steps, then tags the release.

Rollback: re-dispatch `Deploy production` with an older `sha`, or by hand
`aws ecs update-express-gateway-service --service-arn … --task-definition-arn <previous>`
and `aws ecs update-service --cluster <app> --service <app>-<env>-worker --task-definition <previous>`.

## Recovery

Nothing runs on a timer (decision deferred: TODO `scheduling-decision`), so
the system does two things instead: it makes stuck state visible, and it
gives the operator a button.

- **Sentry**: an ERROR is logged the moment the system knows it cannot
  settle something on its own - a checkout whose provider response was lost
  (`payment_checkout_outcome_unknown`), a refund the provider accepted but
  did not settle (`payment_refund_pending_at_provider`), a refund whose
  outcome is unknown (`payment_refund_needs_reconciliation`), a signed event
  that could not be bound to a row.
- **Admin**: the Payments and Deliveries sidebar items carry a badge with
  the count of rows that need a human; the same rows are the
  "Needs attention" list filter. Payments: PENDING for more than two hours
  (past every hosted-session lifetime), or REFUND_PENDING already sent to
  the provider. Deliveries: in progress for more than 30 minutes (a worker
  died mid-batch), or a transactional send the provider rejected.
- **Buttons**: a pending payment has **Verify with provider** (applies the
  provider's answer through the same guarded transition a webhook takes);
  the Deliveries list has **Re-queue stuck deliveries**; a broadcast has
  **Resume incomplete** on its own page. Accepted-not-settled refunds are
  confirmed in the provider dashboard (the row carries the refund id).
- Housekeeping (`manage.py clearsessions`, `prune_db_task_results
  --min-age-days 14`) is run by hand via `just infra-run-task` when the
  tables grow.

## Operations

- **One-off commands**: `just infra-run-task <env> python manage.py <cmd>`
  (worker family, on-demand, waits and returns the exit code).
- **Shell**: `aws ecs execute-command --cluster <app> --task <id> --container Main --interactive --command "python manage.py shell"`.
- **Logs**: `aws logs tail /aws/ecs/<app>-<env>-web --follow` (JSON; grep a
  `request_id` to follow one request). Worker: `…-worker`.
- **Spot**: a reclaimed worker task (any environment) gets SIGTERM two
  minutes ahead; `db_worker` finishes the current job and ECS starts a
  replacement, so queued work is delayed a few minutes, never lost. If an
  app ever runs long, non-restartable jobs, set `worker_spot=False` for that
  environment.
- **When CDK rolls services**: any change to the task definition template
  (env value, size, secret key) registers a revision with the *live* image tag
  and triggers a canary on web and a rolling update on the worker.
- **Secrets**: rotate `SECRET_KEY` by moving the old value into
  `SECRET_KEY_FALLBACKS`; edit `<env>/<app>` in Secrets Manager, then
  `just infra-deploy <env>` (a new revision is needed for ECS to re-read).
- `manage.py axes_reset` clears admin lockouts; admin lives at `ADMIN_URL`.

## Custom domain

Express Mode has no first-class custom domain. With `EnvConfig.custom_domain`
set, CDK does what the AWS migration guide prescribes: an ACM certificate
(DNS-validated in the Route 53 zone) added to the Express HTTPS listener, the
host added to the Express-owned listener rule (`ModifyRule` custom resource),
and a Route 53 alias record to the ALB. If an Express update ever rewrites
the rule's conditions, re-run `just infra-deploy <env>` or add the host in
the console (EC2 → Load balancers → listener rules).

## Renaming stacks deployed before the app-name prefix

Stacks deployed before 2026-08-29 were named `Shared` / `App-<env>` /
`Db-<env>`; CDK now names them `<app>-Shared` / `<app>-App-<env>` /
`<app>-Db-<env>`. CloudFormation cannot rename a stack, and the old `Shared`
owns physically named resources (ECR `eram/<app>`, cluster `<app>`, role
`<app>-github-deploy`) that the new stack must own instead, so it is a
recreate, in this order:

1. `npx cdk destroy App-<env>` for every env (stateless; the retained log
   groups/buckets of production block recreation — delete or `cdk import`
   them). `Db-<env>` (if any) keeps its instance and secrets: `cdk import`
   them into `<app>-Db-<env>` rather than recreating.
2. `npx cdk destroy Shared` (turn off termination protection first). The ECR
   repository is RETAINed: `npx cdk import <app>-Shared` to adopt it, or
   delete it and rebuild the image.
3. The migration history was squashed to one `0001_initial` per app on the
   same day: a database migrated under the old files will refuse the new
   ones. For dev, drop and recreate `<app>_dev` on the shared instance
   (step 6 of the bootstrap) before the next release task runs; a production
   database that predates the squash keeps the old files instead.
4. `just infra-deploy-shared`, then `just infra-deploy-first <env> <sha>`,
   then refresh the GitHub environment variables from the new stack outputs
   (`ECS_CLUSTER` and the families keep their names; the role ARN too).

## Migrating an existing App Runner app

1. Copy `infra/` with its own `AppConfig` (name, repo), deploy `<app>-Shared`
   + `<app>-App-production` alongside the App Runner service.
2. Point `custom_domain` at the existing hostname; CDK adds the certificate,
   rule and DNS alias — switch the DNS record when the Express URL is verified.
3. PostgreSQL 17 → 18: new instance + `pg_dump | pg_restore` (uuidv7 defaults
   need 18); or keep the app on its PG17 instance if it does not use PG18
   features.
4. Delete the App Runner service and, once nothing else uses it, the NAT
   gateway.
