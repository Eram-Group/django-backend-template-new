# Starting a new project from this template

Everything below is a deliberate per-project decision; the rest of the repo
is meant to be used as-is. Work top to bottom — later steps quote values
chosen in earlier ones. Account-level values (AWS account, VPC, subnets,
hosted zone, OIDC provider, DB security group) stay exactly as they are for
every app deployed into the same account.

## 1. Identity

| What | Where |
|---|---|
| Project name (`name`), description | `pyproject.toml` |
| `APP.name` — prefixes every AWS resource and stack (`<name>-Shared`, `<name>-App-<env>`, ECR `eram/<name>`, bucket `eram-<name>-<env>`, secret `<env>/<name>`) | `infra/backend_infra/config.py` |
| `APP.github_repo` — the only repo whose Actions may assume the deploy role | `infra/backend_infra/config.py` |
| `SITE_NAME` (admin header, email branding) and `ACCOUNT_EMAIL_SUBJECT_PREFIX` (the OTP email subject) | `config/settings/base.py` |
| `DJANGO_SUPERUSER_EMAIL`, `DEFAULT_FROM_EMAIL` | `infra/backend_infra/config.py` (`_plain_env`) and `.env.example` |
| **`ADMIN_URL`** — pick a fresh random path; the template's value is public | `infra/backend_infra/config.py` (`_plain_env`) and `.env.example` |
| Per-environment `base_url`, `hosts`, `custom_domain`, `BACKEND_BASE_URL`, frontend origins | `infra/backend_infra/config.py` (`ENVIRONMENTS`) |
| Admin colour ramp and language flags | `config/settings/base.py` (`UNFOLD["COLORS"]`, `EXTENSIONS`) |

## 2. Languages

The template is Arabic-first (`LANGUAGE_CODE = "ar"`, `LANGUAGES = ar/en`,
`MODELTRANSLATION_DEFAULT_LANGUAGE = "ar"`), ships a compiled `locale/ar`
catalog, and the Dockerfile fails the build if that catalog is missing. To
change the language set: edit those three settings, `apps/users/constants.py`
(`Language`), `apps/payments/constants.py` (`CURRENCY_BY_LANGUAGE` must cover
every member), `apps/common/tests/fake.py` (mimesis locales), the Dockerfile
`compilemessages` check, and `just messages`.

## 3. Optional modules

`apps/users` + `apps/common` are the core. The two domain apps are complete
reference implementations of one product's stack — keep, trim, or delete:

- **`apps/payments`** — Tap (SAR) + Paymob (EGP) gateways, wallet ledger,
  saved cards, refunds, `reconcile_payments` cron. Deleting it also means:
  `apps/users/services/users.py` (`wallet_create` at signup), the
  `PAYMENT_GATEWAYS`/`TAP_*`/`PAYMOB_*` settings in `config/settings/base.py`,
  the `TAP_*`/`PAYMOB_*`/`BACKEND_BASE_URL` fields in `config/env.py` and
  `.env.example` (`test_env_contract` keeps them in sync), the
  `_payment_keys_match_environment` validator, the Payments sidebar section in
  `UNFOLD`, the `reconcile-payments` entry in `SCHEDULES`, the payments
  `ignore_imports` lines in `pyproject.toml`, and `apps.payments` in
  `INSTALLED_APPS` / `apps/common/tests/factories_registry.py`.
- **`apps/notifications`** — inbox, FCM push, OurSMS (SA) + SMSMisr (EG)
  routing, WhatsApp Cloud API (webhook only; the send connector is a stub),
  broadcasts, `sweep_deliveries` cron. Same checklist shape: the
  `OURSMS_*`/`SMSMISR_*`/`FIREBASE_*`/`WHATSAPP_*` env fields and settings,
  the Notifications sidebar section, the `sweep-deliveries` schedule, the
  notifications `ignore_imports` lines, `INSTALLED_APPS`, the registry, and
  `apps/users/services/users.py` (`notify` on signup) plus the
  `templates/admin/notifications` + `static/` composer assets.

`uv run lint-imports`, `apps/common/tests/test_env_contract.py`,
`infra/tests/test_env_coverage.py` and `just infra-test` all fail loudly on
anything left half-removed.

## 4. GitHub

Repo variables/secrets: `AWS_ECR_REPOSITORY=eram/<name>`,
`AWS_OIDC_ROLE_ARN` (`<name>-Shared` output), `AWS_REGION`; the Apidog trio
(`APIDOG_PROJECT_ID`, `APIDOG_SERVER_URL`, secret `APIDOG_ACCESS_TOKEN`) or
remove the `apidog` job from `.github/workflows/deploy-dev.yml`. Branch
ruleset on `main` as described in the README. Then follow the
docs/DEPLOYMENT.md bootstrap in order — do not push to `main` before the
`dev` GitHub environment holds every `ECS_*`/`EXPRESS_*` variable.

## 5. Housekeeping

- `PLAN.md` and `TODO.json` are this template's own design log and backlog.
  Replace them with the new project's, or delete them and drop the two
  references in `CLAUDE.md` / `README.md`.
- Migrations: the apps ship their full history, including data migrations
  that will find no rows in a fresh database. They apply cleanly; squash
  only before the first deploy (`notifications/0005` seeds the
  `NotificationKindConfig` rows the catalog requires — keep that seed).
- Run every gate before the first commit: `just lint`, `just typecheck`,
  `just test`, `uv run lint-imports`, `just infra-test`.
