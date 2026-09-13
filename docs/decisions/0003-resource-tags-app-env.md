# 0003. Every AWS resource carries `app`, `env`, `managed-by`

Date: 2026-09-05 · Status: accepted

## Context

Ten apps share one AWS account. Hand-made resources carried `App`/`Env`/
`ManagedBy` (values `prod`, `console`, `cli`), the template's stacks carried
`app`/`env`/`managed-by`, and only the lowercase keys were activated as
cost-allocation tags, so the per-app cost view in Cost Explorer showed
almost nothing. Naming was inconsistent too (`1kcoffee` vs `1k`).

## Decision

Three lowercase tags on everything that can carry one:

| Key | Values |
|---|---|
| `app` | the app name; `shared` for account-level resources |
| `env` | `dev` \| `staging` \| `production` \| `shared` |
| `managed-by` | `cdk` \| `manual` |

Account-level resources (NAT, bastion, hosted zone, CloudTrail bucket, VPC
connector, DB security group) are `shared`/`shared`; the two shared database
instances keep the env they serve. Resources shared across environments but
owned by one app (ECR repository, ECS cluster) carry `app` without `env`.
The legacy keys were removed in one pass on 2026-09-05; CDK applies the
tags to its own stacks.

## Consequences

- Cost Explorer splits the bill per app and environment from the day of
  tagging (tags are not retroactive).
- A resource without both `app` and `env` is invisible in the per-app view:
  creating one by hand means tagging it by hand.
- `prod`, `App`, `Env`, `ManagedBy` are never used again.
