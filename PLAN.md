# Django Production Scaffold — Plan

> Living document — edit freely; every part open to enhancement.

## Goal

API-only Django backend (+ Django admin) for web SPA + mobile clients, deployed as a single Docker image to AWS ECS. Priorities: clean code, clear modern architecture, minimal infrastructure (**Postgres + S3 only**).

## Stack

| Area | Choice | Design notes |
| --- | --- | --- |
| Framework | Django 6.0.x | 6.2 LTS lands ~Apr 2027 — plan the hop. |
| Python | 3.14 | `requires-python = ">=3.14"`, `.python-version`. |
| Packaging | uv | Dev deps in PEP 735 `[dependency-groups]`; commit `uv.lock`. |
| Lint/format | ruff (both) | `E,W,F,I,UP,B,DJ,C4,PIE,SIM,RUF` + `DTZ,T20,PT,S` (`S101` allowed in tests); isort `force-single-line`; migrations excluded. |
| Types | mypy `strict = true` + django-stubs + pydantic plugin | Only migrations exempted. |
| Config | pydantic-settings `Env` in `config/env.py` + dj-database-url (parser only) | `Env` owns every `.env` field: typed (`SecretStr` secrets, `Literal` environment), fail-fast at startup, loads `.env`. All fields REQUIRED with no code defaults — local values live in `.env.example` (copied to `.env`); `X \| None` fields are feature toggles (OAuth/Sentry/S3/cookie domain), absence = feature off. dj-database-url only converts DB URL → `DATABASES` dict. Settings split: `config/settings/{base,local,production,test}.py` consume `env`. |
| API | Django Ninja at `/api/v1/` | OpenAPI docs at `/api/v1/docs` — staff-gated (`docs_decorator`), open in local; assembled in `config/api/v1.py` from per-app routers. |
| Auth | django-allauth[headless-spec,socialaccount] — passwordless | Login + verification by 6-digit email code (`ACCOUNT_LOGIN_BY_CODE_REQUIRED`; custom adapter generates numeric codes; users have no passwords) + Google/Apple social from env creds (settings-based `SOCIALACCOUNT_PROVIDERS`, auto-connect on verified email, OTP skipped for social). Two client types: browser (cookies + CSRF + CORS allowlist) and app (`X-Session-Token`). No JWT. `HEADLESS_ONLY=True`, `HEADLESS_FRONTEND_URLS`, auth OpenAPI spec served; `allauth.usersessions` (list/revoke sessions). Signup kill-switch: `ACCOUNT_ALLOW_REGISTRATION` env → adapter `is_open_for_signup`. |
| Admin UI | django-unfold + in-house admin framework | `BaseModelAdmin` (unfold) forces explicit `can_add`/`can_change`/`can_delete` per admin; field-level rules via `FieldPermissions` + `AdminContext`; unfold Tabular/Stacked inlines (hidden on add view); django-import-export per entity (`resource.py`). Per-entity `admin/<entity>/` packages (admin, list_view, change_view, display, permissions, resource) scaffolded by `generate_dashboard` + CHECKLIST. Clean re-implementation for Django 6 + strict mypy — concepts ported, legacy code is not (no compat aliases, no deprecated helpers). |
| DB | PostgreSQL 18 (compose, CI, RDS) + `psycopg[binary,pool]` | Prod: native pool (min/max size, timeout, max_lifetime, max_idle — all from env) **with `CONN_MAX_AGE = 0`** (required pair); the pool owns connection health — no `CONN_HEALTH_CHECKS` (that's for persistent-connection setups). |
| Tasks | `django.tasks` + `django-tasks-db` backend | Queue in Postgres; worker = `manage.py db_worker` as 2nd ECS service (same image, different command). Enqueue only via `transaction.on_commit`. Cron = EventBridge Scheduler → ECS run-task (`clearsessions` + prune old task results + sample command). Escape hatch: swap backend, call sites unchanged. |
| Cache/sessions | DatabaseCache + DB sessions | `createcachetable` in release step. Extra LocMem cache alias `ratelimit`. |
| Static/media | S3 for both via django-storages + CloudFront | Static = `S3ManifestStaticStorage` (hashed names), media = `S3Storage`. `collectstatic` = release step, never image build. Local dev: Django default static serving. |
| Server | Gunicorn WSGI sync | Workers via `WEB_CONCURRENCY` env (gunicorn-native; tune per ECS task size, default `(2×cores)+1`), `--max-requests`+jitter, access logs → stdout. |
| Email | Anymail[amazon-ses] | `EMAIL_TIMEOUT = 5`. Local: Mailpit in compose (SMTP :1025, web UI :8025) — verification emails visible in a browser. |
| Observability | django-structlog (JSON prod / pretty dev), sentry-sdk (`environment` tag, `send_default_pii=False`), django-guid, django-health-check | `/healthz` liveness (no DB) + `/readyz` readiness (DB/cache/storage). Worker: process-alive check. `SENTRY_TRACES_SAMPLE_RATE` env knob (default 0). Silence `django.security.DisallowedHost` logger. |
| Security | `check --deploy` = zero warnings (CI-enforced) | Argon2 password hashers (staff/superusers keep passwords for admin). Native CSP + nonces, HSTS, secure cookies with `__Secure-` name prefixes in prod, `CSRF_TRUSTED_ORIGINS` = CORS origins, `COOKIE_DOMAIN` env for subdomain auth, `SECRET_KEY_FALLBACKS`, django-axes, django-cors-headers, env-based admin path (optional env-gated `secure_admin_login` → admin via allauth email-OTP). Secret scanning: gitleaks in pre-commit + TruffleHog full-history CI job. django-ratelimit on the LocMem alias — counters are per container (~N× ceiling with N tasks); upgrade = Redis alias or WAF. |
| Tests | pytest, pytest-django, factory_boy, xdist, cov (80% gate), mock, django-test-migrations | Per-app `tests/`; `test.py` uses tasks ImmediateBackend, fast hashers, no-migrations option. pytest: `--reuse-db`, `--import-mode=importlib`. Root `conftest.py`: autouse `MEDIA_ROOT`→tmpdir + `user` fixture. Cross-app gates in `apps/common/tests/`: factory-coverage (every concrete model has a registered factory — explicit per-app registry, loud failures, no auto-discovery magic) + the admin basics gate (dedicated section below). |
| Boundaries | import-linter in pre-commit + CI | Contracts below. |
| Dev UX | pre-commit (ruff-check, ruff-format, django-upgrade, gitleaks, `uv lock --check` + hygiene: trailing-whitespace, end-of-file, check-{yaml,toml,json}, check-added-large-files, merge-conflict, debug-statements, detect-private-key; pre-push stage: branch-behind-main check + `makemigrations --check`) + justfile | `.editorconfig` + `.gitattributes` (`* text=auto`) committed. |
| Docker | Builder `ghcr.io/astral-sh/uv:python3.14-bookworm-slim`, runtime `python:3.14-slim` | Image = the ECS artifact (web/worker share it). Two-phase `uv sync --locked --no-dev` with BuildKit cache mount (uv cache) + bind-mounted `uv.lock`/`pyproject.toml`; `UV_COMPILE_BYTECODE=1`, `UV_PYTHON_DOWNLOADS=0`; non-root. compose (web + worker + postgres:18 + mailpit, `develop: watch:`) = local dev + CI smoke only. PG18 image moved its data dir — volume mounts `/var/lib/postgresql/18/docker`. |
| CI/CD | GitHub Actions + Renovate | Hybrid. Fast job (`setup-uv`): `pre-commit/action` (one lint source with local hooks) → mypy → `uv lock --check` → `makemigrations --check` → pytest (postgres:18 service). Parallel job: docker build (GHA layer cache) → compose up → smoke `/healthz`, `/readyz`, one API call. Secret-scan workflow: TruffleHog full-history. On main: build + push ECR image tagged `github.sha` via GitHub OIDC (no static AWS keys) — the deploy artifact. `concurrency` cancel-in-progress. |
| i18n / TZ | AR/EN + django-modeltranslation; `TIME_ZONE = "UTC"` | `USE_I18N` + LocaleMiddleware; `LANGUAGES = ar/en`; `language` field on User drives email/notification language; translated model fields via per-app `translation.py` (`_ar`/`_en` columns) for content models; `locale/` dir; `makemessages`/`compilemessages` flow (compile at image build). UTC everywhere — clients/admin convert at display. |
| Environments | staging + production | Same settings module; env vars differ; Sentry `environment` tag. |

## Architecture rules

- **Layering per app:** `models/` (data + simple invariants) → `selectors/` (reads) → `tasks/` (async units; bodies use models/selectors only — business logic stays in services, which enqueue via `on_commit`) → `services/` (writes/business logic; typed keyword-only `<entity>_<action>` functions) → `schemas/` + `apis/` (thin routers) + `management/commands/` (thin wrappers calling services).
- **Packages from day one:** every layer is a package split by entity; `__init__.py` re-exports = the app's public interface. Leaf modules (`admin.py`, `constants.py`, `exceptions.py`) start flat, promoted when they grow.
- **BaseModel** (`apps.common`): UUIDv7 pk (`UUIDField(primary_key=True, db_default=Func(function="uuidv7"))`) + `created_at` (indexed) + `updated_at`. All models inherit it, including custom `User` (email login; no username; single `name` field — no first/last; `language` ar/en driving user-facing emails/notifications).
- **Pagination:** cursor-based, defined once in `apps/common/pagination.py`, ordered by UUIDv7 pk; every list endpoint declares it.
- **No signals:** services call services — first-party code never communicates through Django signals. At a third-party boundary, prefer the library's adapter/hook surface (e.g. allauth adapters' `save_user` → `user_post_signup`); a signal receiver is a last resort reserved for libraries that offer no better hook.
- **Errors:** services raise `ApplicationError` (from `apps.common.exceptions`); `config/api/exception_handlers.py` maps all errors to `{"message": ..., "extra": {"fields": ...}}`.
- **Validation:** `full_clean()` in services before save; cross-field/relational rules in services.
- **Bounded contexts:** one app = one domain; cross-app access only via the other app's services/selectors; `apps.common` = primitives only, imports from no domain app.
- **Explicit endpoints:** one hand-written function per endpoint (route + schemas + auth). No viewsets/controllers/CRUD generators. Allauth's mounted auth endpoints are the one exception (surface trimmed in settings).
- **Translated content:** content models needing AR/EN register fields in a per-app `translation.py` — modeltranslation adds the `_ar`/`_en` columns. The API stays single-field: client sends `Accept-Language: ar|en` → LocaleMiddleware activates it → reading `obj.name` returns that language's value automatically (empty → `MODELTRANSLATION_FALLBACK_LANGUAGES`); schemas declare plain `name: str`. Admin edits both columns. User-facing strings use `gettext_lazy`. Emails/notifications render in `user.language` (no request header exists in a worker).
- **Schemas:** outputs = `Summary` and `Detail(Summary)` per entity, each paired with a selector that fetches only what it serializes; add smaller `Ref` only when an embed needs it. Inputs = separate `CreateIn` (required) and `UpdateIn` (all-optional, PATCH via `exclude_unset`), never inheriting each other.
- **Admin:** interface layer like `apis`, built on the unfold `BaseModelAdmin` framework — every admin declares `can_add`/`can_change`/`can_delete` explicitly; field-level visibility/editability via `FieldPermissions` + `AdminContext`. Reads = admin querysets (`get_queryset()` + `select_related`); trivial side-effect-free edits may save directly; anything with business meaning calls the service (actions / `save_model()`). Never duplicate logic in admin. New entities: `manage.py generate_dashboard <app> <Model>` scaffolds the `admin/<entity>/` package.

## Layer enforcement (static, CI-fails on violation)

`lint-imports` runs in pre-commit and CI:

```toml
[tool.importlinter]
root_package = "apps"

[[tool.importlinter.contracts]]        # layer direction inside every app
name = "Layered app internals"
type = "layers"
layers = ["apis | admin | management", "services", "tasks", "selectors", "models"]
containers = ["apps.users"]            # append each new app

[[tool.importlinter.contracts]]        # apps stay independent
name = "Domain apps independent"
type = "independence"
modules = ["apps.users"]               # + future apps
```

Plus a forbidden contract: `apps.common` imports from no domain app.

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
├── pyproject.toml ├── uv.lock      ├── renovate.json
├── README.md      ├── CLAUDE.md    └── docs/ARCHITECTURE.md
├── config/
│   ├── env.py                  # typed Env (pydantic-settings)
│   ├── api/
│   │   ├── v1.py               # NinjaAPI: mounts app routers at /api/v1
│   │   └── exception_handlers.py
│   ├── urls.py                 # admin (env path), api v1, healthz/readyz, allauth headless
│   ├── wsgi.py / asgi.py
│   └── settings/{__init__,base,local,production,test}.py
└── apps/
    ├── common/
    │   ├── models.py           # BaseModel (UUIDv7 pk, created_at, updated_at)
    │   ├── exceptions.py       # ApplicationError hierarchy
    │   ├── pagination.py       # cursor pagination default
    │   ├── admin/              # BaseModelAdmin (unfold), FieldPermissions, AdminContext, base inlines
    │   ├── management/commands/  # generate_dashboard (+ generators/), seed_db
    │   ├── tests/              # cross-app gates: factory coverage + admin basics gate
    │   └── health.py           # healthz/readyz
    └── users/                  # THE template every future app copies
        ├── apps.py  ├── constants.py  ├── exceptions.py  ├── adapters.py
        ├── admin/       user/ {admin,list_view,change_view,display,permissions,resource}.py
        ├── models/      __init__.py + user.py
        ├── migrations/
        ├── schemas/     __init__.py + users.py   # UserUpdateIn, UserSummary, UserDetail
        ├── services/    __init__.py + users.py   # user_post_signup, user_update…
        ├── selectors/   __init__.py + users.py   # user_get, user_list
        ├── apis/        router.py + users.py
        ├── tasks/       __init__.py + emails.py  # @task send_welcome_email
        ├── management/commands/sample_scheduled_job.py
        └── tests/       factories.py + services/ selectors/ apis/
```

## Implementation steps

> Starting point (done): the cookiecutter-django output now IS the repo root — git repo on `main`, uv env synced (Python 3.14.2, Django 6.0.7), pre-commit hooks installed, `psycopg[c]`→`[binary]` (host build fix; matches this design). The steps below TRANSFORM that base toward this design; commit the untouched base first so every change is diffable.

1. ~~Init~~ done (see starting point). Remaining from this step: baseline git commit before transformation begins.
2. Deps — runtime: django>=6.0,<6.1, django-ninja, psycopg[binary,pool], pydantic-settings, dj-database-url, django-allauth[headless-spec,socialaccount], argon2-cffi, django-unfold, django-import-export, openpyxl, django-tasks + django-tasks-db (names confirmed from Gawdat template; verify Django 6 native-tasks wiring), django-modeltranslation, django-storages[s3], gunicorn, django-anymail[amazon-ses], sentry-sdk, django-structlog, django-guid, django-health-check, django-axes, django-ratelimit, django-cors-headers. Dev: ruff, mypy, django-stubs[compatible-mypy], pytest, pytest-django, pytest-xdist, pytest-cov, pytest-mock, pytest-sugar, factory-boy, django-test-migrations, import-linter, pre-commit, django-upgrade, django-debug-toolbar, django-extensions, werkzeug[watchdog] (runserver_plus), ipdb.
3. `config/env.py`: typed `Env(BaseSettings)` — `SecretStr` secrets, `Literal` environment, DB pool sizes, `DJANGO_SUPERUSER_*` bootstrap creds, `COOKIE_DOMAIN`, `FRONTEND_ALLOWED_ORIGINS`, Google/Apple OAuth creds; `.env` loading; all fields required (no code defaults, local values in `.env.example`), optional `X | None` fields = feature toggles.
4. Settings package: base (unfold + modeltranslation ordered before `contrib.admin`; middleware guid→security→session→locale→…→axes; `LANGUAGES` ar/en, `TIME_ZONE="UTC"`, `LOCALE_PATHS`; DATABASES via dj-database-url + env pool knobs + `conn_health_checks`; DatabaseCache + LocMem `ratelimit` alias; TASKS db backend; DB sessions; STORAGES; structlog; CSP; `ATOMIC_REQUESTS=True`; Argon2 `PASSWORD_HASHERS`; `EMAIL_TIMEOUT=5`; passwordless allauth block; `CSRF_TRUSTED_ORIGINS` = CORS origins; `ACCOUNT_ALLOW_REGISTRATION` toggle), local (DEBUG, Mailpit email, toolbar + Docker `INTERNAL_IPS` trick, django-extensions, ImmediateBackend toggle), production (security block, `__Secure-` cookie names, pool + `CONN_MAX_AGE=0`, S3, SES, Sentry, JSON logs), test (fast hashers, ImmediateBackend, in-memory email, no-migrations option). `config/api/`, `urls.py`; `manage.py` → local; mypy/pytest → test.
5. `apps.common`: BaseModel, ApplicationError, cursor pagination, health endpoints; admin framework (unfold `BaseModelAdmin` with abstract `can_*`, `FieldPermissions`, `AdminContext`, base inlines); `generate_dashboard` scaffolder; `seed_db` command; superuser bootstrap via Django-native `createsuperuser --noinput` (`DJANGO_SUPERUSER_*` env) behind an idempotent just recipe; factory-coverage + admin basics test gates.
6. `apps.users`: full template app per tree — `admin/user/` package via `generate_dashboard`, passwordless adapter (6-digit codes, skip-OTP-for-social, `is_open_for_signup`). Signup happens via allauth: the adapters' `save_user` hooks (account + social) call the service, which enqueues the welcome-email task via `on_commit` — no signals. Users API = `/me` endpoints (GET/PATCH) — no duplicate signup route.
7. Auth: allauth headless, passwordless — login/verification by 6-digit email code + Google/Apple social from env creds; browser + app clients; Ninja auth class validating cookie or `X-Session-Token`; CORS + `CSRF_TRUSTED_ORIGINS` from env; `HEADLESS_ONLY`, `HEADLESS_FRONTEND_URLS`, usersessions; optional env-gated `secure_admin_login`.
8. Tooling: ruff/mypy/pytest/coverage/import-linter config in `pyproject.toml`; pre-commit; justfile (bootstrap [uv sync + copy `.env.example`→`.env` + pre-commit install incl. pre-push + migrate], up, stop, logs [service], manage [passthrough], test, lint, fmt, typecheck, migrate [+createcachetable], makemigrations, shell, worker, seed, db-reset [warned], bash, clean, check-deploy).
9. Docker: multi-stage Dockerfile (CMD gunicorn; worker overrides command); `.dockerignore`; compose (web watch, worker, postgres:18 with PG18 volume path, mailpit). Release step = `migrate` + `createcachetable` + `collectstatic`.
10. CI: `.github/workflows/ci.yml` (hybrid: fast uv job + parallel image-smoke job per Stack table), secret-scan workflow (TruffleHog), main-branch ECR push (OIDC, `github.sha` tag); `renovate.json`.
11. Docs: README (quickstart, commands, AWS: two ECS services, release task, RDS PG18, S3+CloudFront, Secrets Manager, staging/production env vars, EventBridge cron recipe); `docs/ARCHITECTURE.md` (rules above, conventions, swap paths); `CLAUDE.md` (rules digest + commands for Claude Code); `.env.example` (every var incl. `ENVIRONMENT`).
12. Run verification; initial git commit.

## Verification

1. `uv sync`; `manage.py check`; `makemigrations --check --dry-run` clean.
2. `docker compose up -d db` → `migrate` + `createcachetable` → `runserver`: `/api/v1/docs`, `/healthz`, `/readyz`, admin all OK.
3. Signup via allauth code flow → user created, welcome-email task enqueued after commit; `db_worker` processes it (email visible in Mailpit); task row in admin; `manage.py sample_scheduled_job` runs. Auth: request login code (visible in Mailpit) → confirm code → browser cookie session; app flow → `X-Session-Token` → `GET /users/me` 200; unauthenticated → 401 `{"message"}`. Ratelimited endpoint returns 429 on burst.
4. `pytest` green (incl. factory-coverage + admin basics gates); coverage ≥ 80%.
5. `ruff check`, `ruff format --check`, `mypy`, `lint-imports`, `pre-commit run --all-files` clean.
6. `check --deploy` with production settings → zero warnings.
7. `docker compose up --build` (web/worker/db) healthy; same smokes; non-root image.

## Notes

- Pin ranges, not exact patches; `uv add` resolves latest compatible.
- Never read `os.environ` in settings — extend the typed `Env`.
- Keep `CSRF_COOKIE_HTTPONLY = False` — the SPA must read the CSRF cookie for headless auth; don't "harden" it later.
- Local SPA dev across ports: use a Vite dev proxy (same-origin) or the app-client token flow — never CSRF-exempt endpoints or disable CSRF locally.
- Verify at install: `django-tasks` + `django-tasks-db` (names confirmed from Gawdat template, backend `django_tasks_db.DatabaseBackend`; check Django 6 native-tasks wiring vs backport); allauth↔Ninja integration path; pytest 9 native `[tool.pytest]` table syntax.
- Deferred (add-later patterns documented in ARCHITECTURE.md): soft-delete/audit history (django-simple-history), S3 presigned uploads, `Ref` schema tier, Redis/WAF rate-limit upgrade, collectfasta if release-step `collectstatic` gets slow, PostGIS path (postgis image + libgdal + `contrib.gis`), gevent worker class for external-API fan-out, User+profile split (Customer/Provider OneToOne) for multi-persona products, unfold insights dashboard, conventional commits (commitizen + PR/commit naming CI).
