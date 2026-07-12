# Architecture

How this codebase is organized and the conventions every change must follow.
This documents **implemented reality**; the original design rationale lives
in [PLAN.md](../PLAN.md).

## Request lifecycle

```
client ──▶ config/urls.py
             ├─ /api/v1/…      ninja API (config/api/v1.py, auth = session cookie OR X-Session-Token)
             ├─ /_allauth/…    allauth headless auth endpoints (passwordless, 6-digit email codes)
             ├─ /<ADMIN_URL>   Django admin (unfold, apps.common.admin framework)
             └─ /healthz /readyz   liveness (no DB) / readiness (DB+cache+storage checks)
```

An API request hits a **thin router function** (`apps/<app>/apis/`), which
calls a **selector** (read) or **service** (write) and returns model objects
that a **schema** serializes. Errors anywhere in that chain surface through
one envelope (below).

## App layout and layering

Every domain app (`apps/users` is the reference, `apps/example` the template)
is a set of packages, one module per entity inside each:

```
apps/<app>/
├── models/        data + simple invariants (all inherit common.BaseModel)
├── selectors/     reads - every Detail/Summary schema pairs with one
├── tasks/         async units; bodies use models/selectors ONLY
├── services/      writes/business logic; typed keyword-only <entity>_<action>()
├── schemas/       ninja Schemas (I/O shapes)
├── apis/          routers - no business logic
├── admin/         one package per entity (generate_dashboard scaffolds it)
├── management/    thin commands calling services
└── tests/         factories.py + test modules
```

**Import direction is machine-enforced** (`uv run lint-imports`, pre-commit + CI):

```
apis | admin | management  →  services  →  tasks  →  selectors  →  models
```

- `tasks` sits BELOW `services` because services enqueue tasks
  (`transaction.on_commit`); task bodies never import services.
- Apps are independent: cross-app access only through the other app's
  services/selectors re-exports, decided explicitly.
- `apps.common` is primitives only and imports no domain app (exceptions:
  `apps/common/tests/` — the cross-app gates and factory registry — and the
  `seed_db` command, both exempted by name in `pyproject.toml`).
- **No signals** in first-party code — services call services. At third-party
  boundaries use the library's hook surface: allauth reports signups through
  `AccountAdapter.save_user` → `user_post_signup` (`apps/users/adapters.py`),
  not the `user_signed_up` signal.

## BaseModel

`apps/common/models.py` — every concrete model inherits it, including `User`:

- `id`: UUIDv7 pk generated **in the database**
  (`db_default=Func(function="uuidv7")`, PG18 native). Time-ordered, so pk
  order ≈ creation order (pagination relies on this).
- `created_at` (indexed) / `updated_at`.

`User` is email-login only: no username, single `name` field, `language`
(ar/en) that drives every user-facing email/notification.

## Error contract

Every API error has ONE shape, produced in ONE place
(`config/api/exception_handlers.py`):

```json
{"message": "<human-readable>", "extra": {"fields": {"<field>": ["msg", ...]}}}
```

- Field errors always live under `extra.fields` with **list** values;
  non-field errors use the `non_field_errors` key (Django's `__all__` is
  normalized to it).
- Services raise `ApplicationError` (`apps/common/exceptions.py`,
  `status_code` class attr, default 400); domain apps subclass it
  (`UserError`). Model validation (`full_clean()` in services) → 400;
  request-schema validation → 422; ninja's 401/403/429 all subclass
  `HttpError` and map through one handler.
- The generic `Exception` handler is deliberately NOT overridden: with
  `DEBUG=False` ninja re-raises, so Django and Sentry own real 500s.
- Unmatched `/api/*` URLs return the same envelope from the project-level
  `handler404` (`config/urls.py`).

## Schemas and pagination

- Outputs: `Summary` and `Detail(Summary)` per entity, each paired with a
  selector fetching exactly what it serializes. Add a smaller `Ref` tier only
  when an embed needs it (deferred until then).
- Inputs: `CreateIn` (required fields) and `UpdateIn` (all-optional) are
  **separate classes** — never inheriting each other. PATCH endpoints apply
  `payload.dict(exclude_unset=True)` so absent ≠ null.
- Services guard writes with an explicit allowlist
  (`USER_UPDATABLE_FIELDS`) + `full_clean()` — the schema is not the only
  gate.
- List endpoints declare `@paginate(CursorPagination)`
  (`apps/common/pagination.py`): cursor over the UUIDv7 pk, newest first,
  opaque urlsafe-base64 cursor, `limit` 1–100 (default 20). A bad cursor is
  an `ApplicationError`, not a 500.

## Auth

- Passwordless: signup/login issue **6-digit email codes** (adapter
  overrides); regular users hold unusable passwords; staff/superusers keep
  Argon2 passwords for the admin.
- allauth headless serves the auth API at `/_allauth/` (spec at
  `/_allauth/openapi.json`, `HEADLESS_ONLY`). After signup the pending flow
  is `login_by_code` — the emailed code doubles as email verification.
- The ninja API accepts **either** the session cookie (browser SPA) **or**
  `X-Session-Token` (mobile/app clients) — `config/api/auth.py`.
- Signup kill-switch: `ACCOUNT_ALLOW_REGISTRATION` env →
  `AccountAdapter.is_open_for_signup`.
- Brute-force lockout: django-axes, 5 failures per (username, ip), 1h
  cooloff, disabled in tests. Rate limiting: django-ratelimit over a LocMem
  cache alias (per-container; Redis/WAF is the documented upgrade).

## Background tasks and commands

Django 6 native `django.tasks` with the Postgres queue (django-tasks-db) —
no broker, the worker is `manage.py db_worker` from the same image.

Conventions (see `apps/users/tasks/emails.py`):

- Task signatures take **pk strings**, never model instances; the body
  re-fetches (the row may have changed since enqueue).
- Tasks are **idempotent** — a retried/duplicated run must be safe.
- Services enqueue via `transaction.on_commit(lambda: task.enqueue(...))` —
  a rolled-back write must never produce a task run.
- Task bodies render user-facing text with
  `translation.override(user.language)` — there is no request in a worker.
- `TASKS_IMMEDIATE=true` (local/test) swaps in the ImmediateBackend: tasks
  run inline, no worker needed.
- Scheduled work = management commands triggered by EventBridge Scheduler →
  ECS RunTask (see README runbook): `clearsessions`,
  `prune_db_task_results --min-age-days 14`, plus app commands modeled on
  `sample_scheduled_job` (thin wrappers over services, safe to re-run).

## i18n and translated content

- Site default Arabic (`LANGUAGE_CODE = "ar"`, `LANGUAGES = ar/en`), UTC
  everywhere — clients and the admin convert for display.
- **API strings**: `Accept-Language: ar|en` → LocaleMiddleware. Code uses
  `gettext_lazy`.
- **Content models** (none yet — pattern ready): register translatable
  fields in a per-app `translation.py`; modeltranslation adds `_ar`/`_en`
  columns. The API stays single-field (`name: str`): reading `obj.name`
  returns the active language's value with fallback per
  `MODELTRANSLATION_FALLBACK_LANGUAGES`; the admin edits both columns.
- **Script validators**: attach `apps/common/validators.py`
  (`validate_arabic_text` / `validate_english_text`) to `_ar`/`_en` content
  columns — wrong-script entry is the classic bilingual-admin data bug, and
  the validators fire in admin forms and services' `full_clean()` alike.
- **modeltranslation edges** (from production use of the pattern):
  must-translate models set `required_languages = ("ar", "en")` in their
  `TranslationOptions` (fallback-only models omit it); translated admins mix
  unfold's `TabbedTranslationAdmin` AFTER `BaseModelAdmin` in the MRO; shadow
  columns are created `null=True` and inherit `unique` from the base field;
  fixtures/factories must set the suffixed fields. `FieldPermissions` rules
  on a base field automatically govern its `_ar`/`_en` shadows — the admin
  framework expands them (`expand_translation_shadows`).
- **Emails/notifications** render in `user.language`, not a request header.
- `.po` catalogs under `locale/`; `makemessages` / `compilemessages`
  (compile happens at image build once catalogs exist).

## Admin framework

`apps/common/admin/` wraps unfold's `ModelAdmin` (details in the class
docstrings; proven by the admin-basics gate):

- Every admin **must declare** `can_add` / `can_change` / `can_delete` —
  missing flags fail at import. Intermediate base classes opt out with
  `abstract_admin = True` in their own body. The same discipline applies to
  inlines (`BaseTabularInline`/`BaseStackedInline`); `ReadOnly*Inline`
  variants are the `can_* = False` preset for display-only child rows.
- `FieldPermissions(readonly_when=…, hidden_when=…)` drives per-request
  field behavior through `AdminContext` (`ctx.is_add`, `ctx.is_change`,
  `ctx.user`, `ctx.is_superuser`) — in admins AND inlines. Hidden is
  airtight: filtered from form, fieldsets, list_display, AND stripped from
  POSTs; rules auto-cover modeltranslation `_ar`/`_en` shadow columns.
  `list_editable` may never name a ruled field (import-time failure — the
  changelist formset ignores per-request rules).
- `created_at`/`updated_at` auto-readonly; M2M fields get the horizontal
  widget automatically (custom-`through` fields excluded); inlines hide on
  the add view unless `show_on_add = True`.
- Exports: `ExportableModelAdmin` + a `BaseModelResource` subclass whose
  `Meta.fields` MUST be explicit. Operators pick columns per run
  (selectable-fields form); offered formats = `EXPORT_FORMATS` (CSV, XLSX).
  Resources humanize output (`column_name=`, `DateTimeWidget`) — exports go
  to non-engineers.
- Changelists: `search_help_text` says what search matches; date ranges via
  unfold's `RangeDateFilter` + `list_filter_submit = True`; expandable
  per-row related-record previews via `list_sections` +
  `apps.common.admin.LimitedTableSection` (ordering + row cap; see
  `apps/users/admin/user/sections.py`).
- State transitions in the admin = unfold detail actions: `actions_detail`
  + `@action(permissions=["…"])` + a `has_<name>_permission` hook for
  state-conditional visibility. The action body **calls a service**, never
  `obj.save()` — pair with `can_change = False` for triage-inbox admins
  where the action is the only write path. Covered by the basics gate.
- Sidebar items live in `UNFOLD["SIDEBAR"]` with a permission callable per
  item (gate-enforced consistency). unfold also supports live `badge`
  callables (dotted path, `request -> str`, `""` hides) — use a selector
  behind it and mind that it runs a query per admin page render.
- New entity → `manage.py generate_dashboard <app> <Model>` scaffolds the
  8-file `admin/<entity>/` package + checklist.
- Reads may use admin querysets; anything with business meaning calls the
  service — logic is never duplicated in admin.
- Third-party admins get re-registered when their defaults don't fit
  (`apps/users/admin/user_session.py`: allauth's session admin searched the
  removed `username` field).

## Testing

- pytest + factories (see `.claude/rules/factories.md` for the full
  conventions: factory_boy structure, mimesis values, explicit registry).
- Cross-app gates in `apps/common/tests/`: **factory coverage** (every
  concrete model registered) and the **admin basics gate** (every registered
  admin exercised generically — no per-admin tests, ever).
- Coverage floor 80% (`[tool.coverage.report]`), enforced in CI. Warnings
  are errors (`filterwarnings = ["error"]`).

## Deferred paths (decided, not built)

Documented so they're added the intended way when needed:

| Need | Path |
|---|---|
| Soft-delete / audit history | django-simple-history on the models that need it |
| Direct-to-S3 uploads | presigned-URL endpoint in a service; client uploads, API stores the key |
| Embedded mini-schemas | add the `Ref` tier per entity alongside Summary/Detail |
| Real rate limiting | move the `ratelimit` cache alias to Redis, or push to WAF |
| Slow release-step collectstatic | collectfasta |
| Geo | postgis image + libgdal in Dockerfile + `django.contrib.gis` |
| External-API fan-out | gunicorn gevent worker class for the web service |
| Multi-persona users | keep one `User`, add OneToOne profile models (Customer/Provider) |
| Admin dashboards | unfold insights/KPI components on the index |
| Release notes discipline | commitizen + conventional-commit CI naming checks |
