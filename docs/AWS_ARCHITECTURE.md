# AWS architecture — the concept

One Docker image, run three ways, on top of Postgres and S3. Everything else
is AWS doing the plumbing.

```
            you push to main
                   │
                   ▼
   GitHub builds the image ──▶ ECR (image registry)
                   │
     1. release task   runs migrations, then
     2. web            gets the new image (canary, auto-rollback), then
     3. worker         gets the new image
                   │
                   ▼
users ──▶ Load balancer ──▶ [ web ]  the API + admin
                                 │
files ◀── CloudFront ◀── S3      ├──▶ PostgreSQL   the only database:
                                 │                 data, cache, sessions, task queue
scheduler ──▶ [ cron jobs ]      │
                                 ▼
              [ worker ]  runs background tasks from the queue
```

## The pieces

| Piece | What it is | AWS service |
|---|---|---|
| **web** | The Django API and admin behind HTTPS. Scales from 1 task up when CPU is busy. | ECS Express Mode (Fargate) |
| **worker** | The same image running `db_worker`, draining the task queue. One small task, always on. | ECS Fargate Spot |
| **cron jobs** | Management commands on a timer (clean sessions, reconcile payments, …). Each run is a short-lived task; nothing is always on. | EventBridge Scheduler |
| **release task** | Runs before every deploy: deploy checks, migrations, static files. If it fails, nothing rolls out. | one-off ECS task |
| **database** | One PostgreSQL 18. It is also the cache, the session store and the task queue — so there is no Redis. | RDS |
| **files** | Uploads and static assets in a bucket only the CDN can read; anyone with a file's URL can fetch it (there is no signed-URL path yet - keep sensitive uploads out until it exists). | S3 + CloudFront |
| **email** | Transactional email. | SES |
| **secrets** | One secret per environment holding keys and passwords, injected into the tasks. | Secrets Manager |
| **image registry** | Where the built image lives. | ECR |

## How it scales and stays up

- **Web** scales out on CPU and back down to one task. Deploys are canaries:
  a bad image is rolled back automatically.
- **Worker** is one task on Fargate Spot (spare capacity, ≈ 70 % off, can be
  reclaimed with two minutes' notice). If it dies it is restarted; if it is
  interrupted it finishes the current task first. Work is never lost — it sits
  in Postgres; at worst it is delayed a few minutes.
- **Cron** is not a server; it is a schedule that starts a task and stops.
- **Database** has daily backups (7 days in production) and can't be deleted
  by accident.

## Environments and apps

Each app has `dev` and `production` (optionally `staging`). Every
environment is its own copy of web + worker + cron + bucket + secrets.
Production has its own database; all dev environments of all apps share one
small database server. All web services in the account share one load
balancer.

## What it costs

- **dev** ≈ $23/month, **production** ≈ $49/month per app, plus a share of the
  two shared pieces (load balancer ≈ $34, dev database ≈ $30 — split across
  ~5 apps, the divisor docs/DEPLOYMENT.md uses).
- Roughly **$85/month per app** for dev + production once those shares are
  counted (the per-environment detail is in docs/DEPLOYMENT.md), versus
  ≈ $105–120 on the previous App Runner setup.

## Why these choices

- **ECS Express Mode** instead of App Runner: App Runner is closed to new
  customers; Express is the replacement and adds autoscaling + canary
  rollback for free.
- **RDS** instead of Aurora Serverless: 3.5× cheaper for a small always-on
  app, and Aurora's scale-to-zero never triggers while a worker is connected.
- **Postgres as cache/queue** instead of Redis: one less service to pay for
  and operate; the load doesn't justify it.
- **No scale-to-zero on web**: a load balancer needs at least one running
  task; the floor is ≈ $10/month, not worth engineering around.

Operational detail (bootstrap, deploy commands, cost table, levers):
[DEPLOYMENT.md](DEPLOYMENT.md). Code: [`infra/`](../infra).
