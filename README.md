# Backend

API-only Django backend: [django-ninja](https://django-ninja.dev) REST API +
Django admin (unfold), passwordless auth (allauth headless, 6-digit email
codes), Postgres-only infrastructure (cache, sessions, task queue), one
Docker image deployed as two ECS services. Arabic-first (ar/en).

The authoritative design document is [PLAN.md](PLAN.md); the remaining build
sequence lives in [TODO.json](TODO.json).

| | |
|---|---|
| Runtime | Python 3.14, Django 6.0, django-ninja (API at `/api/v1/`), allauth headless (`/_allauth/`) |
| Data | PostgreSQL 18 (db-generated uuidv7 pks, DatabaseCache, db sessions, django.tasks db backend) |
| Storage / email | S3 + CloudFront via django-storages; SES via Anymail (Mailpit locally) |
| Quality | ruff, mypy --strict, pytest (coverage gate ≥ 80%), import-linter contracts, pre-commit |
| Deploy | Single image → ECS Fargate (web + worker) via GitHub Actions OIDC; dev auto-deploys, production promotes |

## Quickstart

Prerequisites: [uv](https://docs.astral.sh/uv/), [just](https://just.systems),
Docker (for Postgres + Mailpit).

```bash
just bootstrap   # deps, .env, git hooks, postgres:18 + mailpit, migrate + cache table
just superuser   # admin@example.com / admin (from .env, idempotent)
just seed        # ~300 realistic fake users (see "Seed data" below)
just run         # dev server on http://localhost:8000
```

| URL | What |
|---|---|
| http://localhost:8000/api/v1/docs | API docs (Swagger; staff-only outside local) |
| http://localhost:8000/admin/ | Admin (unfold) — `admin@example.com` / `admin` |
| http://localhost:8000/_allauth/openapi.json | Auth API spec (allauth headless) |
| http://localhost:8025 | Mailpit — every local email lands here |
| http://localhost:8000/healthz, /readyz | Liveness / readiness |

The full production-shaped stack (gunicorn web + task worker, production
settings module) runs with `just up` — on a fresh database apply the release
step first:
`docker compose run --rm web sh -c "python manage.py migrate && python manage.py createcachetable"`.

## Commands

Run `just` for the full list. The ones you'll use daily:

| Recipe | Does |
|---|---|
| `just run` | runserver_plus with the local settings module |
| `just test [args]` | pytest (append paths/flags as needed) |
| `just lint` / `just fmt` / `just typecheck` | ruff check / ruff format / mypy --strict |
| `just manage <cmd>` | any manage.py command |
| `just migrate` / `just makemigrations` | migrations (+ cache table) |
| `just seed [scale]` | fake data; scale 0..1 is logarithmic (0 = 10 users, 1 = 1,000,000) |
| `just worker` | drain the task queue (`manage.py db_worker`) |
| `just shell` | shell_plus |
| `just db-reset` | destroy volumes + re-migrate (asks first) |
| `just check-deploy` | `manage.py check --deploy` against production settings |

## Testing & quality gates

`just test` runs pytest with `--reuse-db`; CI adds `--cov` with a hard
**80% coverage floor** (`[tool.coverage.report] fail_under`). Two cross-app
gates in `apps/common/tests/` keep the codebase honest as it grows:

- **Factory coverage** — every concrete model must have a factory registered
  in `apps/common/tests/factories_registry.py` (explicit dict, no discovery
  magic). Adding a model without one fails the suite.
- **Admin basics** — every registered admin (current and future) is
  exercised automatically: changelist/search/filters/sorting, permission-aware
  object pages, an unchanged save round-trip, CSV export, sidebar permission
  consistency, and a hidden-field tamper check. No per-admin tests are ever
  written.

Architecture boundaries are machine-checked by import-linter (layers:
`apis|admin|management → services → tasks → selectors → models`; domain apps
independent; `apps.common` imports no domain app).

## Seed data

`just seed [scale]` populates the local database through the factory
registry — factory_boy for structure, mimesis for values (Arabic users get
ar-sa names). Related rows fan out per parent with variance. Seeded rows
carry the `@seed.example.com` email domain: `manage.py seed_db --wipe ...`
removes exactly them, `--seed N` makes runs deterministic. Refuses to run
unless `ENVIRONMENT=local`. Conventions: `.claude/rules/factories.md`.

## Background tasks

Django 6 native `django.tasks` with the Postgres-backed queue — no broker.

- Locally `TASKS_IMMEDIATE=true` runs tasks inline; flip it to exercise the
  real queue and drain with `just worker`.
- Deployed, the **worker service** runs `manage.py db_worker` from the same
  image. SIGTERM is graceful: the worker finishes its current task before
  exiting — set the ECS `stopTimeout` (worker task definition) to at least
  your longest task.
- Results are rows: prune with `manage.py prune_db_task_results
  --min-age-days 14` (scheduled below).

## Configuration

`config/env.py` is the only reader of `os.environ` — typed, and **every field
is required with no code defaults**. [.env.example](.env.example) is the
canonical reference; a missing key fails at boot listing every missing field.
Empty value on the `X | None` fields = feature off (Sentry, OAuth, cookie
domain). Deployed environments provide the same keys through ECS task
definitions with secrets pulled from Secrets Manager.

## Deployment (AWS runbook)

One image, three run modes: **web** (gunicorn), **worker** (`db_worker`),
**release task** (`migrate` + `createcachetable` + `collectstatic`, run
before each rollout from the exact revision being deployed). Nothing runs at
container boot; the image never runs collectstatic at build time.

### One-time provisioning

1. **ECR** repository for the image.
2. **RDS** PostgreSQL 18; one database; `DATABASE_URL` into Secrets Manager.
3. **S3** bucket (static + media prefixes via django-storages) fronted by
   **CloudFront** (`AWS_S3_CUSTOM_DOMAIN`); **SES** verified domain out of
   sandbox (`AWS_SES_REGION`).
4. **ECS Fargate** cluster; two task-definition families per environment
   (e.g. `backend-dev-web`, `backend-dev-worker`) whose container is named
   **`app`** — the deploy workflows render by family and container name. Env
   vars per `.env.example` (secrets via Secrets Manager `secrets` entries;
   `SENTRY_RELEASE` is injected by CD, leave it out).
5. Two **services**: web behind an ALB (target group health check
   `/readyz`, deployment **circuit breaker with rollback** enabled) and
   worker (no load balancer, `stopTimeout` sized to your longest task).
6. **EventBridge Scheduler** cron → ECS `RunTask` on the worker family with a
   command override, one schedule per job:

   | Schedule | Command |
   |---|---|
   | daily | `python manage.py clearsessions` |
   | daily | `python manage.py prune_db_task_results --min-age-days 14` |
   | as needed | your scheduled jobs (see `sample_scheduled_job`) |

7. **GitHub OIDC role**: trust policy scoped to this repo; permissions: ECR
   push/pull, `ecs:Describe*`/`RegisterTaskDefinition`/`UpdateService`/`RunTask`,
   `iam:PassRole` on the task execution/task roles.
8. **GitHub environments** `dev` and `production` (add required reviewers on
   `production` for a manual approval gate).

### GitHub variables / secrets

Repo-level: `AWS_ECR_REPOSITORY`, `AWS_OIDC_ROLE_ARN`, `AWS_REGION`,
`DEV_DEPLOY_ENABLED` (sentinel: any value), `APIDOG_PROJECT_ID` (+ secret
`APIDOG_ACCESS_TOKEN`). Per environment (`dev`, `production`): `ECS_CLUSTER`,
`ECS_FAMILY_WEB`, `ECS_FAMILY_WORKER`, `ECS_SERVICE_WEB`, `ECS_SERVICE_WORKER`,
`ECS_SUBNETS`, `ECS_SECURITY_GROUPS`, `ECS_ASSIGN_PUBLIC_IP`. Every deploy
stage skips gracefully until its variables exist — the repo stays green with
zero infra provisioned.

### Pipelines

- **PR / push** — `ci.yml`: lint (pre-commit) → mypy → lock check →
  migrations check → pytest (coverage ≥ 80%, postgres:18 service), plus a
  parallel prod-image build + compose smoke (`/healthz`, `/readyz`, auth spec).
  TruffleHog secret scan; Renovate keeps dependencies fresh.
- **Merge to main** — `deploy-dev.yml`: build + push `:sha` (OIDC, GHA layer
  cache) → render web task def (`SENTRY_RELEASE=sha`) → **release task** from
  that revision, then roll web (waits for stability) → roll worker → sync the
  OpenAPI schema to Apidog. Deploys queue, never cancel mid-rollout.
- **Production** — `deploy-prod.yml` (manual dispatch, choose
  patch/minor/major): computes the next `v*` semver → **promotes the exact
  dev image** by manifest retag (build once, no rebuild) → release task +
  roll web/worker → git tag + GitHub Release last, so a tag only ever points
  at a fully deployed sha.

### Operations notes

- Logs are JSON (structlog) with a per-request `request_id`
  (`Correlation-ID`) — grep one id in CloudWatch to follow a request.
- `django-axes` locks a (username, ip) pair for 1h after 5 failed admin
  logins: `manage.py axes_reset` clears lockouts.
- Secret rotation: put the new `SECRET_KEY`, move the old one into
  `SECRET_KEY_FALLBACKS`, drop it after sessions expire.
- The admin lives at `ADMIN_URL` — randomize it outside local, and consider
  `SECURE_ADMIN_LOGIN=true` (email-code admin login).
