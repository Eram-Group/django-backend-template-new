# Django Production Scaffold — Plan

> Living document — edit freely; every part open to enhancement.

## Goal

API-only Django backend (+ Django admin) for web SPA + mobile clients, deployed as a single Docker image to AWS ECS (Express Mode web + Fargate worker, CDK in `infra/`, runbook `docs/DEPLOYMENT.md`). Priorities: clean code, clear modern architecture, minimal infrastructure (**Postgres + S3 only**).

## Stack

| Area | Choice | Design notes |
| --- | --- | --- |
| Framework | Django 6.1.x | 6.2 LTS lands ~Apr 2027 — plan the hop. |
| Python | 3.14 | `requires-python = ">=3.14"`, `.python-version`. |
| Packaging | uv | Dev deps in PEP 735 `[dependency-groups]`; commit `uv.lock`. |
| Lint/format | ruff (both) | 41 explicit rule families (the list and the rationale per family live in `pyproject.toml` + `docs/LINTING.md`; `S101` allowed in tests); isort `force-single-line`; migrations excluded. |
| Types | mypy `strict = true` + django-stubs + pydantic plugin | Only migrations exempted. |
| Config | pydantic-settings `Env` in `config/env.py` + dj-database-url (parser only) | `Env` owns every `.env` field: typed (`SecretStr` secrets, `Literal` environment), fail-fast at startup, loads `.env`. All fields REQUIRED with no code defaults — local values live in `.env.example` (copied to `.env`); `X \| None` fields are feature toggles (OAuth/Sentry/S3/cookie domain), absence = feature off. dj-database-url only converts DB URL → `DATABASES` dict. Settings split: `config/settings/{base,local,production,test}.py` consume `env`. |
| API | Django Ninja at `/api/v1/` | OpenAPI docs at `/api/v1/docs` — staff-gated (`docs_decorator`); assembled in `config/api/v1.py` from per-app routers. |
| Auth | django-allauth[headless-spec,socialaccount] — passwordless | Login + verification by 6-digit email code (`ACCOUNT_LOGIN_BY_CODE_REQUIRED`; custom adapter generates numeric codes; users have no passwords) + Google/Apple social login DEFERRED (TODO `social-providers`; the six `*_OAUTH_*` env fields exist and are wired to ECS, nothing consumes them yet). Two client types: browser (cookies + CSRF + CORS allowlist) and app (`X-Session-Token`). No JWT. `HEADLESS_ONLY=True`, `HEADLESS_FRONTEND_URLS`, auth OpenAPI spec served; `allauth.usersessions` (list/revoke sessions). Signup kill-switch: `ACCOUNT_ALLOW_REGISTRATION` env → adapter `is_open_for_signup`. |
| Admin UI | django-unfold + in-house admin framework | `BaseModelAdmin` (unfold) forces explicit `can_add`/`can_change`/`can_delete` per admin; field-level rules via `FieldPermissions` + `AdminContext`; unfold Tabular/Stacked inlines (hidden on add view); django-import-export per entity (`resource.py`). One module per entity (`admin/<entity>.py`, class attributes for capability flags, field rules, list/change config; resources in `admin/resources.py`) scaffolded by `generate_dashboard` (flattened 2026-09-05 from the earlier seven-file packages). Clean re-implementation for Django 6 + strict mypy — concepts ported, legacy code is not (no compat aliases, no deprecated helpers). |
| DB | PostgreSQL 18 + PostGIS 3.6 (compose/CI `imresamu/postgis:18-3.6`, RDS `postgis` extension) + `psycopg[binary,pool]`; `django.contrib.gis` with the postgis engine (2026-08-30, zones) | GeoDjango binds libgdal/libgeos via ctypes: Homebrew on the host (`GDAL_LIBRARY_PATH`/`GEOS_LIBRARY_PATH` env), `libgdal32 libgeos-c1v5 libproj25` in the image. PostGIS is not an RDS trusted extension - the shared dev database gets `CREATE EXTENSION postgis` from the master user (runbook), production's CDK master user creates it through the migration. Prod: native pool (min/max size, timeout, max_lifetime, max_idle — all from env) **with `CONN_MAX_AGE = 0`** (required pair); the pool owns connection health — no `CONN_HEALTH_CHECKS` (that's for persistent-connection setups). |
| Tasks | `django.tasks` + `django-tasks-db` backend | Queue in Postgres; worker = `manage.py db_worker` as 2nd ECS service (same image, different command). Enqueue only via `transaction.on_commit`. Cron = EventBridge Scheduler → ECS run-task (`clearsessions`, `prune_db_task_results`, `reconcile_payments`, `sweep_deliveries` — `infra/backend_infra/config.py::SCHEDULES`). Escape hatch: swap backend, call sites unchanged. |
| Cache/sessions | DatabaseCache + DB sessions | `createcachetable` in release step. The one cache also holds every rate-limit counter (allauth limits + ninja throttles), so ceilings are global across web tasks. |
| Static/media | S3 for both via django-storages + CloudFront | Static = `S3ManifestStaticStorage` (hashed names), media = `S3Storage`. `collectstatic` = release step, never image build. Local dev: Django default static serving. |
| Server | Gunicorn WSGI sync | `gunicorn.conf.py`: 2 sync workers (sized for the 0.25–0.5 vCPU task), `timeout` 60 s (above the outbound retry window), `max_requests`+jitter, no gunicorn access log (django-structlog logs every request as JSON). |
| Email | Anymail[amazon-ses] | Django 6.1 `MAILERS` (the `EMAIL_*` settings are deprecated, gone in 7.0): one `default` mailer, 5s timeout in `OPTIONS`. Local: Mailpit in compose (SMTP :1025, web UI :8025) — verification emails visible in a browser. |
| Outbound clients (built 2026-07-12) | httpx + stamina (retries) + respx (test mocks); firebase-admin (FCM HTTP v1); django-phonenumber-field | One kernel `apps/common/http.py::request_json` (explicit timeouts, typed retry policies, error taxonomy, structlog). Fixed transports (one per channel, tests swap in in-memory outboxes): SMS = OurSMS SA / SMSMisr EG behind country routing, push = own Device model (NOT fcm-django, it drags DRF), `PAYMENT_GATEWAYS` currency map (Tap SAR / Paymob EGP in every environment — env keys pick test vs live mode, HMAC-verified webhooks, Decimal money, wallet ledger via `wallet_apply` row lock; saved cards 2026-07-18: `SavedCard` token vault — PCI stays at the provider — always-on save at checkout (not client-optional), one-click CIT, service-only MIT via Tap payment-agreement non-3DS / Paymob CoF + MOTO, and Paymob's TOKEN callback verified with its own 8-field HMAC). SMS/push: console locally, real transports only when deployed; tests: locmem outboxes + FakeGateway pinned in test.py. (`apps/notifications` v2, rebuilt 2026-07-27 after the 2026-07-12 build was removed by product decision: in-app inbox + FCM push (firebase-admin 7.x `send_each`, 200-chunk, dead-token pruning) + OurSMS/SMSMisr SMS (country routing, OurSMS bulk `dests`) + WhatsApp plumbing (template-name catalog, Console/Locmem backends, signed status webhook; Meta Cloud API connector is a follow-up PR — placeholder fails loudly). Channel policy resolves in two tiers: a per-broadcast `channels` pick wins (intersected with the kind's supported set, so a withdrawn channel can never resurrect), otherwise the per-kind policy = its `NotificationKindConfig` row — one explicit row per kind holding the channel list AND the admin-editable ar/en title/body (modeltranslation, the repo's first translated model; replaced the catalog-default + sparse `NotificationChannelOverride` layer on 2026-07-28, migration `notifications/0005` seeds the rows from the old defaults (the v2 migrations are additive on top of the v1 `0001`-`0003`: `0004` schema, `0005` data - legacy `*_sent_at` markers become SENT delivery rows - `0006` drops the markers); the catalog stays the code-side contract — context_keys, supported channels, category, WhatsApp template, seed copy — and message rendering moved to `selectors.messages`, one config query per executor batch / API request, no cross-request cache). ANNOUNCEMENT is `authored_per_send`: its row keeps the `{title}`/`{message}` passthrough and `notification_config_update` refuses message edits — the composer owns that copy. The operator surface is a single-page editor replacing the changelist (`templates/admin/notifications/notificationkindconfig/change_list.html` + `static/{css,js}/notification_config.*`): one card per action with channel switches (unsupported channels rendered disabled), AR/EN copy fields, context-key chips, live preview, per-card fetch save through `config_save_view` → the service; the tabbed-translation change form remains as the no-JS fallback (per-broadcast selection added 2026-07-27 with the compose form, superseding the same-day global-only decision; user-facing preferences remain deliberately not exposed). `NotificationDelivery` rows are the idempotency/recovery backbone: unique (notification, channel), claim PENDING→PROCESSING via skip_locked, monotonic webhook status guard. 100k fan-out: `Broadcast` rows + dispatcher task pages users 5k/transaction (crash-exact cursor), bulk-creates inbox+delivery rows, enqueues ~200-pk batch tasks on a dedicated `bulk` queue (one worker drains `default,bulk`); no auto-retry by design — `broadcast_resume` service/admin action + `sweep_deliveries` command re-enqueue exactly the incomplete remainder. Authoring is the admin compose form (`admin/broadcast/form.py`, the only form class in the project): announcement-only — the other kinds are per-user events whose context cannot be shared — a title/body pair that builds `context={"title": ..., "message": ...}` instead of hand-typed JSON, audience filters (language, registered-device, joined-between) and the channel pick. ANNOUNCEMENT's catalog title became `"{title}"` on 2026-07-27 (it was a fixed `gettext("Announcement")`), so the headline is operator-authored like the body; migration `notifications/0005` backfills the key into rows written under the old shape, which would otherwise KeyError on render. The WhatsApp template still binds one variable — Meta fixed its approved slot count — so the title rides push/SMS/inbox only. The screen itself is a standalone composer (`templates/admin/notifications/broadcast/compose.html` + `static/{css,js}/broadcast_compose.*`, static files because SECURE_CSP's script-src is self+nonce): live reach estimate against `broadcast_audience` itself, live push preview, advisory 65/240 counters, and a confirm modal that refuses an empty audience. Add routes through `notification_broadcast`, so the catalog's context validation actually runs in the admin; `save_model` calling `obj.save()` used to skip it and a malformed announcement only failed inside the worker. Audience reads stay in `selectors.broadcast_audience` — the has-device filter is a subquery, since a join would duplicate rows under the dispatcher's pk cursor and double-send.) |
| Observability | django-structlog (JSON prod / pretty dev; binds request_id/correlation_id itself), sentry-sdk (`environment` tag, `send_default_pii=False`) | `/healthz` liveness (no DB) + `/readyz` readiness (DB/cache — storage stays out of the probe; failures name the check, never the driver error). Worker: process-alive check. `SENTRY_TRACES_SAMPLE_RATE` env knob (default 0). Silence `django.security.DisallowedHost` logger. |
| Security | `check --deploy` = zero warnings (enforced in the deploy release tasks, dev + prod) | Argon2 password hashers (staff/superusers keep passwords for admin). Native CSP + nonces, HSTS, secure cookies with `__Secure-` name prefixes in prod, `CSRF_TRUSTED_ORIGINS` = CORS origins, `COOKIE_DOMAIN` env for subdomain auth, `SECRET_KEY_FALLBACKS`, django-axes, django-cors-headers, env-based admin path (`ADMIN_URL`; admin login = password + django-axes). CSP carries `base-uri`/`form-action`/`frame-ancestors`/`object-src` too. Secret scanning: TruffleHog full-history CI job + `detect-private-key` in pre-commit. Rate limiting: allauth's per-ip/per-key limits on `/_allauth/` + ninja throttles on `/api/v1/` (API-wide per principal, tighter on checkout, signature-verified webhooks exempt), all counted in the shared DatabaseCache; WAF on the ALB is the edge upgrade. |
| Tests | pytest, pytest-django, factory_boy + mimesis (structure vs values; mimesis = fast, ar-sa/en locales), xdist, cov (80% gate); doubles are `monkeypatch` + hand-written fakes (locmem outboxes, FakeGateway), no mock library | Per-app `tests/`; `test.py` uses tasks ImmediateBackend, fast hashers; migrations run for the test DB (they create the PostGIS extension). pytest: `--import-mode=importlib`, fresh test DB per run (`--reuse-db` was dropped 2026-08-30: it saved ~0.5 s and caused schema drift + accumulating session-seeded rows). Root `conftest.py`: autouse `MEDIA_ROOT`→tmpdir + `user` fixture. Cross-app gates in `apps/common/tests/`: factory-coverage (every concrete model has a registered factory — explicit per-app registry, loud failures, no auto-discovery magic) + the admin basics gate (dedicated section below). |
| Boundaries | import-linter in pre-commit + CI | Contracts below. |
| Dev UX | pre-commit (ruff-check, ruff-format, django-upgrade, `uv lock --check` for both projects, actionlint, shellcheck, lint-imports + hygiene: trailing-whitespace, end-of-file, check-{yaml,toml,json}, check-added-large-files, merge-conflict, debug-statements, detect-private-key; pre-push stage: branch-behind-main check + `makemigrations --check`) + justfile | `.editorconfig` + `.gitattributes` (`* text=auto`) committed. |
| Docker | Builder `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`, runtime `python:3.14-slim` + libgdal/libgeos/libproj | Image = the ECS artifact (web/worker share it). Two-phase `uv sync --locked --no-dev` with BuildKit cache mount (uv cache) + bind-mounted `uv.lock`/`pyproject.toml`; `UV_COMPILE_BYTECODE=1`, `UV_PYTHON_DOWNLOADS=0`; non-root. compose = `imresamu/postgis:18-3.6` (multi-arch; the official postgis/postgis image has no arm64) + mailpit only (Django runs on the host: `just run` + `just worker`); CI boots the built image with `docker run` for the smoke. PG18 image moved its data dir — volume mounts `/var/lib/postgresql/18/docker`. |
| CI/CD | GitHub Actions + Dependabot | Hybrid. Fast job (`setup-uv`): `pre-commit/action` (one lint source with local hooks) → mypy → `uv lock --check` → `makemigrations --check` → pytest (postgis 18 service; `libgdal-dev libgeos-dev` on the runner). Parallel job: docker build (GHA layer cache) → release step + `docker run` → smoke `/healthz`, `/readyz`. Secret-scan workflow: TruffleHog full-history. On main (`deploy-dev.yml`): build + push ECR image tagged `github.sha` via GitHub OIDC (no static AWS keys) → release task (check --deploy + migrate + createcachetable + collectstatic, via `amazon-ecs-deploy-task-definition@v2` `run-task` on the freshly rendered worker revision) → roll dev web then worker → Apidog OpenAPI sync after a successful deploy (`export_openapi_schema --api config.api.v1.api` → Apidog import API, OVERWRITE_EXISTING). Prod (`deploy-prod.yml`, manual dispatch, bump input patch/minor/major): next version from latest `v*` tag → promote the exact main image (`buildx imagetools` manifest retag — build once) → release task + roll prod services → git tag + GitHub Release (`--generate-notes`) last. Task defs are created by CDK (`infra/`, 2026-08-28) and CD registers revisions by `task-definition-family` with container `Main`; web rolls through ECS Express Mode (`update-express-gateway-service`, canary + auto-rollback), worker through `UpdateService`; arm64 image built on `ubuntu-24.04-arm`; the deploy workflows expect every GitHub variable to exist and fail loudly on a missing one (bootstrap order in docs/DEPLOYMENT.md). CI `concurrency` cancel-in-progress; deploys never cancel (queued per environment). Stacks are app-name-prefixed (`<app>-Shared`, `<app>-Db-<env>`, `<app>-App-<env>`) because every app shares one account; the GitHub deploy role is scoped to the app's cluster, repository, log groups and roles. |
| i18n / TZ | AR/EN + django-modeltranslation; `TIME_ZONE = "UTC"` | `USE_I18N` + LocaleMiddleware; `LANGUAGES = ar/en`; `language` field on User drives email language; translated model fields via per-app `translation.py` (`_ar`/`_en` columns) for content models; `locale/` dir; `makemessages`/`compilemessages` flow (compile at image build). UTC everywhere — clients/admin convert at display. |
| Environments | dev + production (+ optional staging) | Same settings module; env vars differ; Sentry `environment` tag + `release` = git sha (CD injects `SENTRY_RELEASE` at task-def render — one release per commit across both envs, per build-once-promote). dev auto-deploys on merge to main; production deploys via manual dispatch. |

## Architecture rules

- **Layering per app:** `models/` (data + simple invariants) → `selectors/` (reads) → `tasks/` (async units; bodies use models/selectors only — business logic stays in services, which enqueue inside their transaction - the queue is the database) → `services/` (writes/business logic; typed keyword-only `<entity>_<action>` functions) → `schemas/` + `apis/` (thin routers) + `management/commands/` (thin wrappers calling services). Recorded exception (2026-07-18, `ignore_imports` in pyproject): `payments.tasks.refunds` trampolines into its own app's services — the non-idempotent provider refund call must run in the worker, outside any request transaction, while the money logic stays in services.
- **Packages from day one:** every layer is a package split by entity; `__init__.py` re-exports = the app's public interface. Leaf modules (`admin.py`, `constants.py`, `exceptions.py`) start flat, promoted when they grow.
- **BaseModel** (`apps.common`): UUIDv7 pk (`UUIDField(primary_key=True, db_default=Func(function="uuidv7"))`) + `created_at` (indexed) + `updated_at`. All models inherit it, including custom `User` (email login; no username; single `name` field — no first/last; optional not-unique E164 `phone` passed to payment gateways; `language` ar/en driving user-facing emails).
- **Pagination:** cursor-based, defined once in `apps/common/pagination.py`, ordered by UUIDv7 pk; every list endpoint declares it.
- **No signals:** services call services — first-party code never communicates through Django signals. At a third-party boundary, prefer the library's adapter/hook surface (e.g. allauth adapters' `save_user` → `user_post_signup`); a signal receiver is a last resort reserved for libraries that offer no better hook.
- **Errors:** services raise `ApplicationError` (from `apps.common.exceptions`); `config/api/exception_handlers.py` maps all errors to `{"message": ..., "extra": {"fields": ...}}`.
- **Validation:** `full_clean()` in services before save; cross-field/relational rules in services.
- **Bounded contexts:** one app = one domain; cross-app access only via the other app's services/selectors; `apps.common` = primitives only, imports from no domain app.
- **Explicit endpoints:** one hand-written function per endpoint (route + schemas + auth). No viewsets/controllers/CRUD generators. Allauth's mounted auth endpoints are the one exception (surface trimmed in settings).
- **Translated content:** content models needing AR/EN register fields in a per-app `translation.py` — modeltranslation adds the `_ar`/`_en` columns. The API stays single-field: client sends `Accept-Language: ar|en` → LocaleMiddleware activates it → reading `obj.name` returns that language's value automatically (empty → `MODELTRANSLATION_FALLBACK_LANGUAGES`); schemas declare plain `name: str`. Admin edits both columns. User-facing strings use `gettext_lazy`. Emails render in `user.language` (no request header exists in a worker).
- **Schemas:** outputs = `Summary` and `Detail(Summary)` per entity, each paired with a selector that fetches only what it serializes; add smaller `Ref` only when an embed needs it. Inputs = separate `CreateIn` (required) and `UpdateIn` (PATCH via `ninja.PatchDict[UpdateIn]`, view receives only sent keys), never inheriting each other.
- **Admin:** interface layer like `apis`, built on the unfold `BaseModelAdmin` framework — every admin declares `can_add`/`can_change`/`can_delete` explicitly; field-level visibility/editability via `FieldPermissions` + `AdminContext`. Reads = admin querysets (`get_queryset()` + `select_related`); trivial side-effect-free edits may save directly; anything with business meaning calls the service (actions / `save_model()`). Never duplicate logic in admin. New entities: `manage.py generate_dashboard <app> <Model>` scaffolds `admin/<entity>.py`.

## Layer enforcement (static, CI-fails on violation)

`lint-imports` runs in pre-commit and CI:

Three contracts, kept in `pyproject.toml` (`[tool.importlinter]` - the one
source; every app is listed there, `management` is an optional layer, and
each recorded cross-app edge sits in `ignore_imports` with its reason):
layers inside every app (`apis | admin | (management)` → `services` →
`tasks` → `selectors` → `models`), domain-app independence, and
`apps.common` importing no domain app.

## Admin basics gate

Generic pytest suite in `apps/common/tests/admin/` — iterates `admin.site._registry` so every current AND future admin is covered automatically; no per-admin test is ever written. Permission-aware (superuser client + `has_*_permission` checks so restricted admins skip, not fail), data auto-seeded once per session (autouse fixture over the factory registry), `raise_request_exception=True` so real tracebacks surface:

- Index + every changelist load.
- Search (`?q=`) on every admin with `search_fields` — bad relation lookups only blow up when queried.
- Every `list_filter` rendered AND applied (first choice) — filters fail only when used.
- Sorting every `list_display` column, both directions — catches computed columns without `admin_order_field`.
- Add, change, delete-confirmation, and history pages load for every model (change/delete/history against a real object).
- Unchanged-save round-trip: GET change form → re-POST as-is → must validate and save (catches required/readonly conflicts, form `clean()` breakage, inline management-form misconfig — not just 500s).
- Every import-export resource exports CSV with header + data rows (catches empty `fields=[]` resources).
- Autocomplete endpoint responds for every `autocomplete_fields` target (misconfigured target admin breaks only when the widget queries).
- Data coverage: every registered admin model has seeded rows — an admin tested against an empty table proves nothing.
- Unfold sidebar navigation links all reverse/resolve.

## File tree

```
PythonProject/
├── .github/workflows/ci.yml        ├── manage.py
├── .dockerignore  ├── .gitignore   ├── Dockerfile
├── .env.example (committed)        ├── .env (gitignored; bootstrap copies from example)
├── .python-version  ├── compose.yaml
├── .editorconfig  ├── .gitattributes  ├── conftest.py
├── .pre-commit-config.yaml         ├── justfile
├── pyproject.toml ├── uv.lock      ├── .github/dependabot.yml
├── README.md      ├── CLAUDE.md    └── docs/ARCHITECTURE.md
├── config/
│   ├── env.py                  # typed Env (pydantic-settings)
│   ├── api/
│   │   ├── v1.py               # NinjaAPI: mounts app routers at /api/v1
│   │   └── exception_handlers.py
│   ├── urls.py                 # admin (env path), api v1, healthz/readyz, allauth headless
│   ├── wsgi.py
│   └── settings/{__init__,base,local,production,test}.py
└── apps/
    ├── common/
    │   ├── models.py           # BaseModel (UUIDv7 pk, created_at, updated_at)
    │   ├── exceptions.py       # ApplicationError hierarchy
    │   ├── pagination.py       # cursor pagination default
    │   ├── admin/              # BaseModelAdmin (unfold), FieldPermissions, AdminContext, base inlines
    │   ├── management/commands/  # generate_dashboard, seed_db
    │   ├── tests/              # cross-app gates: factory coverage + admin basics gate
    │   └── health.py           # healthz/readyz
    └── users/                  # THE template every future app copies
        ├── apps.py  ├── constants.py  ├── exceptions.py  ├── adapters.py
        ├── admin/       user.py (one module per entity) + resources.py
        ├── models/      __init__.py + user.py
        ├── migrations/
        ├── schemas/     __init__.py + users.py   # UserUpdateIn, UserSummary, UserDetail
        ├── services/    __init__.py + users.py   # user_create, user_update…
        ├── selectors/   __init__.py               # /me serves request.auth; add a selector with the first list/detail endpoint
        ├── apis/        router.py + users.py
        ├── tasks/       __init__.py + emails.py  # @task send_welcome_email
        └── tests/       factories.py + services/ selectors/ apis/
    └── location/               # reference data: Country (ISO-sourced, loaded from the admin sheet)
        ├── iso.py               # pycountry + babel (CLDR ar/en names, tender currency) + phonenumbers
        ├── models/country.py    # code, alpha_3, name(ar/en), dial_code, phone_example, currency, flag, is_active
        ├── services/countries.py  # countries_load (the ONE creation road), country_flags_fetch
        ├── tasks/flags.py       # fetch_country_flag: flagcdn PNG, fixed filename, after commit
        ├── admin/country/       # can_add=False; "Load countries" list action = the picker sheet
        └── apis/countries.py    # GET /location/countries - public, unpaginated, locale-aware name
```

## Implementation steps

> Starting point (done): the cookiecutter-django output now IS the repo root — git repo on `main`, uv env synced (Python 3.14.2, Django 6.0.7), pre-commit hooks installed, `psycopg[c]`→`[binary]` (host build fix; matches this design). The steps below TRANSFORM that base toward this design; commit the untouched base first so every change is diffable.

1. ~~Init~~ done (see starting point). Remaining from this step: baseline git commit before transformation begins.
2. Deps — runtime: django>=6.0,<6.1, django-ninja, psycopg[binary,pool], pydantic-settings, dj-database-url, django-allauth[headless-spec,socialaccount], argon2-cffi, django-unfold, django-import-export, openpyxl, django-tasks + django-tasks-db (names confirmed from Gawdat template; verify Django 6 native-tasks wiring), django-modeltranslation, django-storages[s3], gunicorn, django-anymail[amazon-ses], sentry-sdk, django-structlog, django-axes, django-cors-headers (no django-guid / django-health-check / django-ratelimit: structlog binds the request id, `readyz` is two inline checks, ninja throttles + allauth limits cover rate limiting). Dev: ruff (pinned to the pre-commit rev), mypy, django-stubs[compatible-mypy], pytest, pytest-django, pytest-xdist, pytest-cov, pytest-sugar, factory-boy, mimesis, import-linter, pre-commit, django-debug-toolbar, django-extensions, ipdb (django-upgrade runs from pre-commit only).
3. `config/env.py`: typed `Env(BaseSettings)` — `SecretStr` secrets, `Literal` environment, DB pool sizes, `DJANGO_SUPERUSER_*` bootstrap creds, `COOKIE_DOMAIN`, `FRONTEND_ALLOWED_ORIGINS`, Google/Apple OAuth creds; `.env` loading; all fields required (no code defaults, local values in `.env.example`), optional `X | None` fields = feature toggles.
4. Settings package: base (unfold + modeltranslation ordered before `contrib.admin`; middleware probe→cors→security→csp→session→locale→…→axes; `LANGUAGES` ar/en, `TIME_ZONE="UTC"`, `LOCALE_PATHS`; DATABASES via dj-database-url + env pool knobs, no `ATOMIC_REQUESTS` (services own `atomic()`); one DatabaseCache (also the rate-limit store); TASKS db backend; DB sessions; STORAGES; structlog; CSP (kept in local too - the toolbar nonces its scripts); Argon2 `PASSWORD_HASHERS`; `MAILERS` with a 5 s timeout; passwordless allauth block; `CSRF_TRUSTED_ORIGINS` = CORS origins), local (DEBUG, Mailpit email, toolbar, django-extensions), production (security block, `__Secure-` cookie names, psycopg pool with Django's default `CONN_MAX_AGE=0`, S3, SES, Sentry, JSON logs), test (fast hashers, ImmediateBackend, in-memory email + cache, FakeGateway). `config/api/`, `urls.py`; `manage.py` → local; mypy/pytest → test.
5. `apps.common`: BaseModel, ApplicationError, cursor pagination, health endpoints; admin framework (unfold `BaseModelAdmin` with abstract `can_*`, `FieldPermissions`, `AdminContext`, base inlines); `generate_dashboard` scaffolder; `seed_db --scale 0..1` command (log curve, 1.0 = 1M users; build_batch + chunked bulk_create through the factory registry, fan-out ratios per child model, @seed.example.com marker, --wipe/--seed, local-only guard); superuser bootstrap via Django-native `createsuperuser --noinput` (`DJANGO_SUPERUSER_*` env) behind an idempotent just recipe; factory-coverage + admin basics test gates.
6. `apps.users`: full template app per tree — `admin/user.py` via `generate_dashboard`, passwordless adapter (6-digit codes, skip-OTP-for-social, `is_open_for_signup`). Signup happens via allauth: the adapters' `save_user` hooks (account + social) call the service, which enqueues the welcome-email task via `on_commit` — no signals. Users API = `/me` endpoints (GET/PATCH) — no duplicate signup route.
7. Auth: allauth headless, passwordless — login/verification by 6-digit email code + Google/Apple social from env creds; browser + app clients; Ninja auth class validating cookie or `X-Session-Token`; CORS + `CSRF_TRUSTED_ORIGINS` from env; `HEADLESS_ONLY`, `HEADLESS_FRONTEND_URLS`, usersessions (admin login stays password + axes). Auth order is load-bearing: the `X-Session-Token` auth runs before the cookie auth, because ninja's cookie auth runs the CSRF check before looking for a cookie.
8. Tooling: ruff/mypy/pytest/coverage/import-linter config in `pyproject.toml`; pre-commit; justfile (bootstrap [uv sync + copy `.env.example`→`.env` + pre-commit install incl. pre-push + migrate], run + worker [the one dev road], stop, logs [service], manage [passthrough], test, lint [pre-commit], fmt, typecheck, migrate [+createcachetable], makemigrations, shell, seed, db-reset [warned], clean).
9. Docker: multi-stage Dockerfile (CMD gunicorn; worker overrides command); `.dockerignore`; compose (postgis 18 with PG18 volume path, mailpit). Release step = `migrate` + `createcachetable` + `collectstatic`.
10. CI/CD: `.github/workflows/ci.yml` (hybrid: fast uv job + parallel image-smoke job per Stack table), secret-scan workflow (TruffleHog), `deploy-dev.yml` (main: OIDC ECR push `github.sha` → dev ECS deploy → Apidog sync), `deploy-prod.yml` (dispatch: bump-input version → promote → deploy → tag + GitHub Release); `.github/dependabot.yml`.
11. Docs: README (quickstart, commands, AWS: two ECS services, release task, RDS PG18, S3+CloudFront, Secrets Manager, dev/production env vars, EventBridge cron recipe; CI/CD runbook: task-def families with container name `Main`, deployment circuit breaker + rollback on both services, OIDC role ECS/ECR perms + `iam:PassRole`, GitHub environments `dev`/`production`, repo vars `APIDOG_PROJECT_ID`/`APIDOG_SERVER_URL` + secret `APIDOG_ACCESS_TOKEN`, env-scoped `ECS_*` vars; branch ruleset on `main`: require PR + status checks `fast`/`image`/`trufflehog` green before merge — deploy-dev deploys main as-is and relies on this gate); `docs/ARCHITECTURE.md` (rules above, conventions, swap paths; migrations must be expand/contract — web rolls before worker by design); `CLAUDE.md` (rules digest + commands for Claude Code); `.env.example` (every var incl. `ENVIRONMENT`).
12. Run verification; initial git commit.

## Status (2026-08-30)

`apps/location` (2026-08-29/30): `Country` rows loaded from ISO data via the
admin sheet, and `Zone` rows (PostGIS `MultiPolygonField`, FK → Country)
loaded from GeoJSON FeatureCollections via the "Load zones" sheet - upsert by
`code` (`<country>-<region>-<zone_code>`), unnamed features land inactive with
the code as their name, `zone_for_point` is one `ST_Contains` query, no API
yet. The PostGIS decision (over JSONField + shapely) was taken against the
real source data: ~1,900 zones across 7 countries, 19 MB of polygons.

Everything above is built and green (ruff, mypy strict, pytest ≥ 80 % with
branch coverage, lint-imports, CDK synth + assertions + cfn-lint, `check
--deploy` zero warnings). The template review of 2026-08-29 rated the repo
68/100 as a starting point and fixed its blockers the same day; what is
still open lives in `TODO.json` (group G24 "Template hardening follow-ups"
plus the product tasks), and `docs/NEW_PROJECT.md` is the adoption
checklist for a new project.

## Verification

1. `uv sync`; `manage.py check`; `makemigrations --check --dry-run` clean.
2. `docker compose up -d db` → `migrate` + `createcachetable` → `runserver`: `/api/v1/docs`, `/healthz`, `/readyz`, admin all OK.
3. Signup via allauth code flow → user created, welcome-email task enqueued after commit; `db_worker` processes it (email visible in Mailpit); task row in admin. Auth: request login code (visible in Mailpit) → confirm code → browser cookie session; app flow → `X-Session-Token` → `GET /users/me` 200; unauthenticated → 401 `{"message"}`. Ratelimited endpoint returns 429 on burst.
4. `pytest` green (incl. factory-coverage + admin basics gates); coverage ≥ 80%.
5. `ruff check`, `ruff format --check`, `mypy`, `lint-imports`, `pre-commit run --all-files` clean.
6. `check --deploy` with production settings → zero warnings.
7. `docker compose up --build` (web/worker/db) healthy; same smokes; non-root image.

## Notes

- Pin ranges, not exact patches; `uv add` resolves latest compatible.
- Countries are loaded, never typed (2026-08-29): the admin "Load countries"
  sheet is the only creation road (no fixture, no data migration, no
  command); every column is copied from `apps/location/iso.py` and flags
  arrive through `fetch_country_flag` after commit. `GET /location/countries`
  is public and unpaginated on purpose (<= 250 rows, alphabetical in the
  active language - `CursorPagination` orders by `-pk`). Linking `User` to a
  country is `TODO.json: users-country-fk`.
- Never read `os.environ` in settings — extend the typed `Env`.
- Keep `CSRF_COOKIE_HTTPONLY = False` — the SPA must read the CSRF cookie for headless auth; don't "harden" it later.
- Local SPA dev across ports: use a Vite dev proxy (same-origin) or the app-client token flow — never CSRF-exempt endpoints or disable CSRF locally.
- Verify at install: `django-tasks` + `django-tasks-db` (names confirmed from Gawdat template, backend `django_tasks_db.DatabaseBackend`; check Django 6 native-tasks wiring vs backport); allauth↔Ninja integration path; pytest 9 native `[tool.pytest]` table syntax.
- Deferred (add-later patterns documented in ARCHITECTURE.md): audit history (django-simple-history) and recoverable delete (`deleted_at` pattern) — two distinct needs, split 2026-07-12; S3 presigned uploads, `Ref` schema tier, WAF edge rate limiting, collectfasta if release-step `collectstatic` gets slow, PostGIS path (postgis image + libgdal + `contrib.gis`), gevent worker class for external-API fan-out, User+profile split (Customer/Provider OneToOne) for multi-persona products, unfold insights dashboard, content-app blueprint (payments built 2026-07-12), conventional commits (commitizen + PR/commit naming CI).
