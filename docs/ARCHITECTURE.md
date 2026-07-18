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

Every domain app (`apps/users` is the reference, `apps/notifications` and
`apps/payments` the built-out examples, `apps/example` the copy-me template)
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
- Inputs: `CreateIn` and `UpdateIn` are **separate classes** — never
  inheriting each other. PATCH handlers take `ninja.PatchDict[UpdateIn]`,
  which auto-optionalizes the schema and delivers only the keys the client
  sent, so absent ≠ null.
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
- **Error-shape decision (2026-07-12)**: `/_allauth/` endpoints keep
  allauth's **native** error format (`{status, errors[]}`) — rewriting
  responses would fork the library's documented contract and its spec.
  `/api/v1/` uses the project envelope. Clients handle both;
  [docs/AUTH_API.md](AUTH_API.md) is the consumer guide that contrasts
  them (including the 400 `too_many_login_attempts` rate-limit case).

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

## Outbound clients (HTTP kernel, SMS, push, payments)

Every call to an external service goes through **one kernel**,
`apps/common/http.py::request_json` — explicit `httpx.Timeout(10, connect=5)`,
a typed retry policy (stamina, 3 attempts, jittered backoff), structured
logs (`outbound_request_ok/failed`; request bodies never logged), and an
error taxonomy (`OutboundTransportError` / `OutboundStatusError`, body
truncated). Retry policies are chosen per call semantics:

| Policy | Retries | Use for |
|---|---|---|
| `transient` | transport errors + 429/5xx | idempotent-enough calls: SMS sends, status GETs (duplicate beats dropped) |
| `connect-only` | only errors raised before the request hit the wire | non-idempotent POSTs: payment charge/refund creation |
| `none` | nothing | everything else |

A 2xx with an error body is the **caller's** job: each provider module owns
an allowlist success predicate (OurSMS accepted/rejected counts, SMSMisr
`code == "1901"`) — unknown shapes fail loudly, never pass silently.

**Transport selection = the `EMAIL_BACKEND` pattern** (settings string,
resolved per call): `SMS_BACKEND` / `PUSH_BACKEND` / `PAYMENT_GATEWAYS`
(currency → gateway class). Base + local = console/fake (structlog lines,
fake checkout URLs); `production.py` swaps in the real transports only when
`_DEPLOYED`; `test.py` = locmem outboxes
(`apps/notifications/clients/*/backends.py::outbox` — the `mail.outbox`
analogue), so tests can never touch provider HTTP even with real creds in
`.env`. Provider clients live in leaf packages
(`apps/notifications/clients/`, `apps/payments/gateways/`) — unconstrained
by the layer contract, called from services/tasks.

Per-area notes:

- **SMS** (OurSMS SA / SMSMisr EG): `RoutingSmsBackend` picks the provider
  from the number's country (`PROVIDER_REGISTRY`); SMSMisr's `language` is
  chosen per message body (Arabic codepoints → "2") and its live/test
  `environment` comes from `ENVIRONMENT`. Adding a provider = one module
  implementing `SmsBackend` + one registry entry.
- **Push** (FCM via firebase-admin, HTTP v1): NOT fcm-django (hard DRF
  dependency). Own `Device` model + `messaging.send_each` in 500-token
  chunks; tokens Firebase reports unregistered come back in
  `PushReport.invalid_tokens` and the delivery task deletes those rows.
  Firebase init is lazy from `FIREBASE_CREDENTIALS_B64` (base64
  service-account JSON). firebase-admin transports its own HTTP.
- **Notifications**: per-recipient inbox rows store `(kind, context)`;
  copy lives in the typed catalog (`apps/notifications/catalog.py`,
  gettext) and renders at send/read time in the viewer's locale — never
  stored pre-rendered. Delivery = `on_commit` tasks with `*_sent_at`
  idempotency markers; a kind's channels are declared on its catalog entry
  (`test_catalog` keeps `NotificationKind` ↔ `CATALOG` in lockstep).
- **Payments** (Tap SAR / Paymob EGP): gateway Protocol + frozen DTOs in
  `apps/payments/gateways/`. Money is Decimal end-to-end; the wire uses
  integer minor units (`to_minor_units`). Charge creation plants
  `Payment.idempotency_key` at the gateway (Tap `reference.transaction`,
  Paymob `special_reference`) — webhooks echo it back and
  `payment_apply_gateway_event` finds the row by it, under
  `select_for_update`, never overwriting terminal statuses (replays ack
  with 200 and cannot re-credit). Webhooks REALLY verify: Tap `hashstring`
  HMAC-SHA256, Paymob `hmac` HMAC-SHA512 over its 20 documented fields,
  constant-time compares. The webhook route
  (`/api/v1/payments/webhooks/{gateway}`) is the API's one deliberate
  `auth=None` surface — signature IS the authentication. Wallet balance
  moves only through `wallet_apply` (Wallet row lock + append-only
  `WalletTransaction` ledger with `balance_after`). Local flow:
  `manage.py simulate_payment_webhook <pk> [--fail]` drives the same
  transition service (Mailpit's role, for payments).

**Cross-app decisions on record** (independence contract `ignore_imports`
in `pyproject.toml`): notifications → users (rows belong to a User;
delivery reads `user.phone`/`user.language`) and payments → users +
payments → notifications (paid events call `notification_send`). Both are
one-way; nothing imports payments.

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

Documented so they're added the intended way when needed. The *Blueprint*
column points into `Gawdat_Django_Template/` — a local, gitignored copy of
a real production template audited file-by-file (2026-07-12); start future
work from that proven code, ported to this repo's conventions (ninja, no
signals, services, strict mypy).

| Need | Path | Blueprint (Gawdat_Django_Template/) |
|---|---|---|
| Audit history (who changed what) | django-simple-history on the models that need it | `apps/payment/models/` (registration), `apps/users/admin/*/admin.py` (SimpleHistoryAdmin MRO) |
| Recoverable delete (user-facing rows kept for history, e.g. addresses referenced by orders) | `deleted_at` pattern or django-softdelete — a DIFFERENT need than audit history; decide the lib when first needed | `apps/location/models/address.py` |
| Direct-to-S3 uploads | presigned-URL endpoint in a service; client uploads, API stores the key | — |
| Embedded mini-schemas | add the `Ref` tier per entity alongside Summary/Detail | — |
| Real rate limiting | move the `ratelimit` cache alias to Redis, or push to WAF | — |
| Slow release-step collectstatic | collectfasta | — |
| Geo (countries/regions/zones) | postgis image + libgdal in Dockerfile + `django.contrib.gis` | `apps/location/` — Region model, geojson loaders (`loadcountries`/`loadzones` + `assets/countries/`), point-in-region lookups |
| External-API fan-out | gunicorn gevent worker class for the web service | — |
| Multi-persona users | one `User` + OneToOne profile models (Customer/Provider). Gate incomplete profiles at the API layer: `is_profile_completed` computed in a selector and exposed on the persona Detail schema, plus a ninja auth class raising an ApplicationError whose envelope carries `action_required: "complete_profile"`. Never path-prefix middleware — exempt lists rot and it bypasses the envelope (the template's own middleware shipped stale and unwired). | `apps/users/` customer/provider models, selectors, setup flows + `tests/test_customer_signup_flow.py` |
| Admin dashboards (index KPIs/charts) | unfold insights components on the index (`DASHBOARD_CALLBACK`) + per-changelist KPI cards | `common/insights/`, `assets/templates/admin/` (index + components), `assets/templates/admin/payment/*/change_list.html` |
| Social login (G13) | settings-based `SOCIALACCOUNT_PROVIDERS` from env creds; social adapter calls `user_post_signup` | `config/settings/base.py` SOCIALACCOUNT block, `config/helpers/allauth_adapter.py` (headless `serialize_user` enrichment) |
| Mobile content app (FAQ/banners/onboarding/contact) | content models with per-app `translation.py` + curated fixtures loaded by a `loadfixtures` command (+ fixtures-loading test) | `apps/appInfo/`, `assets/fixtures/`, `common/management/commands/loadfixtures.py`, `tests/test_fixtures_loading.py` |
| Runtime-editable operational settings | django-constance with unfold widgets; singleton settings/legal-content models via django-solo | `config/integrations/unfold.py` constance block, `apps/appInfo/models/app_info.py` |
| Country reference data | code-only Country model + library-derived metadata (dial codes, flags); customer address book with primary-address invariants | `apps/location/models/country.py`, `domain/utils/country_info.py`, `models/address.py` |
| Public terms/privacy page | one server-rendered page off AppInfo content (the API-only rule's single documented exception) | `common/views/web_view.py`, `assets/templates/terms/` |
| Release notes discipline | commitizen + conventional-commit CI naming checks | `.github/pr-naming.yml`, `commit-naming.yml`, `release.yml` |
| ImageField factories | session-shared tiny test image fixture (avoid generating per-row images) | `tests/conftest.py` image fixture |
