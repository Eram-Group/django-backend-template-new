# 0002. Workers run on Fargate Spot in every environment

Date: 2026-09-05 · Status: accepted

## Context

The worker is a queue consumer: jobs are idempotent, enqueued inside the
writing transaction, and `db_worker` finishes the current job on SIGTERM.
Fargate Spot is ~70 % cheaper and can reclaim a task with two minutes'
notice. The template originally kept production on-demand out of caution;
the three live workers (dars, 1k) had already run on Spot without incident.

## Decision

`worker_spot=True` for every environment, production included. The release
task and scheduled jobs stay on-demand. An app running long, non-restartable
jobs flips the flag for that environment and says why.

## Consequences

- Worker cost per environment ≈ $2.50 instead of $8.29/month.
- A reclaim delays queued work by a few minutes; it never loses it (the
  queue is the database).
- The web tier stays on-demand: Express Mode does not support Spot and a
  reclaimed web task would drop requests.
