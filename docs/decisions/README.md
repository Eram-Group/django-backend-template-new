# Architecture decisions

One file per decision, numbered, never edited after acceptance: a change of
mind is a new record that supersedes the old one. Keep each under a page:
what was the situation, what we decided, what it costs us.

| # | Decision | Status |
|---|---|---|
| [0001](0001-images-webp-thumbnails-cdn.md) | Images: WebP on save, a thumbnail for lists, served through CloudFront | accepted 2026-09-05 |
| [0002](0002-fargate-spot-for-workers.md) | Workers run on Fargate Spot in every environment | accepted 2026-09-05 |
| [0003](0003-resource-tags-app-env.md) | Every AWS resource carries `app`, `env`, `managed-by` | accepted 2026-09-05 |

Template for a new record:

```
# NNNN. Title

Date: YYYY-MM-DD · Status: proposed | accepted | superseded by NNNN

## Context
## Decision
## Consequences
```
