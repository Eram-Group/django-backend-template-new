# Project rules

docs/ARCHITECTURE.md explains the conventions in depth. PLAN.md is the
design log and TODO.json the task tracker.

## Architecture rules (digest)

- Layering per app, enforced by import-linter:
  `apis | admin | management → services → tasks → selectors → models`.
  Apps are independent; `apps.common` imports no domain app.
- Cross-app access: one-way only, each direction recorded in `pyproject.toml`
  `ignore_imports`. Importing another app's **model** is allowed only for
  passive ownership (FK target, read-only — e.g. `User` from payments).
  Anything with behavior or invariants goes through the owning app's
  **service** (e.g. `wallet_apply`) — never mutate another
  app's model directly. FKs use string refs (`"app.Model"`), no import.
- **No signals** in first-party code — services call services. Third-party
  boundaries use the library's hooks (allauth adapter `save_user`, never
  `user_signed_up`).
- Services: typed keyword-only `<entity>_<action>(*, ...)` functions,
  explicit field allowlists, `full_clean()` before save.
- One error envelope `{"message", "extra": {"fields": {...}}}` — raise
  `ApplicationError` subclasses; the mapping lives ONLY in
  `config/api/exception_handlers.py`.
- Schemas: `Summary` / `Detail(Summary)` outputs paired with selectors;
  `CreateIn` and `UpdateIn` are separate classes; PATCH handlers take
  `ninja.PatchDict[UpdateIn]` (auto-optionalizes; view receives only
  sent keys). Lists paginate with
  `apps/common/pagination.CursorPagination`.
- **Env discipline**: `config/env.py` is the only `os.environ` reader; every
  field required, no code defaults; a new field updates `.env.example` in
  the same change (test_env_contract enforces the sync).
- Tasks (`django.tasks`): trampolines that take pk strings and call one
  service (function-level import, recorded in `pyproject.toml`); the work
  is idempotent; services enqueue INSIDE their transaction (the queue is
  this database - the task row commits or rolls back with the write);
  `transaction.on_commit` is only for external side effects. Nothing runs
  on a timer (TODO `scheduling-decision`): a state the system cannot settle
  itself logs an ERROR (Sentry) and shows under the admin's "Needs
  attention" badge/filter, with a manual recovery action beside it.
- Deploy: ECS Express Mode (web) + Fargate (worker); the task-definition
  container is named `Main` — load-bearing for Express Mode and the CD
  render steps, never rename. `ENVIRONMENT` ∈ local/dev/staging/production.
  Stacks per app, app-name-prefixed because every app shares one account:
  `<app>-Shared`, `<app>-Db-<env>` (stateful, dedicated RDS), `<app>-App-<env>`
  (stateless) — names in `infra/backend_infra/naming.py`. Account-level
  resources (GitHub OIDC provider, shared dev RDS, DB security group) are
  referenced by value in `AppConfig`, never created — this template is
  copied into many apps and none may own them.
- Admin: subclass `apps.common.admin.BaseModelAdmin`; declare
  `can_add/can_change/can_delete`; field rules via `FieldPermissions`; one
  module per entity (`admin/<entity>.py`, resources in `admin/resources.py`);
  scaffold new admins with `manage.py generate_dashboard <app> <Model>`.
- Arabic-first: `gettext_lazy` for strings; user-facing emails render in
  `user.language`; content models use per-app `translation.py`.
- Models inherit `apps.common.models.BaseModel` (db-generated uuidv7 pk);
  register a factory for every new model (coverage gate).

## Daily commands

- `just bootstrap` (one-time) · `just run` (dev server) + `just worker`
  (the one dev road; compose runs only postgres + mailpit)
- `just test` · `just lint` (pre-commit, the single lint source) · `just fmt` ·
  `just typecheck` · `uv run lint-imports`
- `just manage <cmd>` · `just migrate` · `just makemigrations` ·
  `just seed <scale> <seed>` · `just superuser` · `just messages` (Arabic
  catalog) · `just branch <name>`
- AWS (CDK in `infra/`, design `docs/AWS_ARCHITECTURE.md`, runbook
  `docs/DEPLOYMENT.md`): `just infra-synth` ·
  `just infra-test` · `just infra-lint` · `just infra-diff <env>` ·
  `just infra-deploy <env>` · `just infra-run-task <env> <cmd>`
- Before finishing any change, run the gates: `just gates` (lint, mypy,
  lockfile, migrations check, pytest, OpenAPI) and `just infra-gates` — all
  must be green (coverage floor 80%, warnings are errors).

## Factories & seed data

Stack: factory_boy 3.3+ (structure) + mimesis 19+ (values). Faker is only a
transitive dependency of factory_boy — never import it.

### Writing a factory

- One factory per concrete model, in `apps/<app>/tests/factories.py`,
  subclassing `DjangoModelFactory[Model]` (the generic subscript is
  supported at runtime and keeps mypy strict happy).
- `class Meta` must set `skip_postgeneration_save = True` (3.3+ behavior:
  avoids the deprecated second save after post_generation) and
  `django_get_or_create = [...]` on the natural key (e.g. email).
- Structure comes from factory_boy declarations (`Sequence`,
  `LazyAttribute`, `LazyFunction`, `SubFactory`, `Trait` under
  `class Params`, `fuzzy.FuzzyChoice` for weighted/random picks — never
  `LazyFunction(lambda: random.choice(...))`, which bypasses factory_boy's
  reseedable RNG); every fake VALUE comes from `apps/common/tests/fake.py`
  (mimesis `Locale.AR_SA` / `Locale.EN` providers — Arabic users get Arabic
  values). Add new value helpers there, never instantiate mimesis/Faker in
  a factory.
- Factories satisfy runtime invariants, not just DB constraints. UserFactory
  is the reference: verified primary `EmailAddress` via `post_generation`
  (code-login pends on verify_email without it), `password = "!"`
  (passwordless invariant), locale-matched `name`, weighted `language`.
- `post_generation` hooks must no-op when `create` is False —
  `Factory.build()` must never touch the database.
- **Related objects: use `RelatedFactory`, not a hand-rolled
  `post_generation` hook** (the official recipes recommendation for
  reverse FK/O2O invariants — verified against docs + 3.3.3 source,
  2026-07-18). Exemplar: `UserFactory.wallet`, mirroring the signup
  invariant (`user_create` → `wallet_create`):
  - Pass the factory as a **dotted-path string**
    (`RelatedFactory("apps.payments.tests.factories.WalletFactory",
    factory_related_name="user")`) when a class import would be circular —
    factory_boy resolves it lazily at first use (`_FactoryWrapper`).
  - `factory_related_name` passes the parent instance as that kwarg, which
    also suppresses the child factory's `SubFactory` — so two factories can
    reference each other without recursion.
  - The parent's strategy propagates: `.build()` builds the child too,
    still zero DB queries. `post_generation` remains the tool only where no
    dedicated construct exists (e.g. many-to-many).
- Unsaved instances of BaseModel have `pk` set to a `DatabaseDefault`
  sentinel (db-generated uuidv7), **not** `None` — assert
  `obj._state.adding`, never `obj.pk is None`.
- Call factories as `UserFactory.create(...)` / `.build(...)` — never
  `UserFactory(...)`. Explicit reads better and mypy (no factory_boy stubs)
  types the dunder-call as a factory instance, not the model.
- Register the factory in
  `apps/common/tests/factories_registry.py::FACTORIES` in the SAME change
  that adds the model — the coverage gate
  (`apps/common/tests/test_factory_coverage.py`) fails the suite otherwise.
  The registry is an explicit dict; no auto-discovery, no swallowed errors.

### Bulk seeding (seed_db)

- `manage.py seed_db --scale 0..1` — log curve (0 → 10 users, 0.5 → ~3.2k,
  1.0 → 1,000,000); `just seed` defaults to 0.3. Local-only guard
  (`ENVIRONMENT=local`); dev-only imports stay inside `handle()` so the
  module imports cleanly in production images.
- At scale, NEVER `Factory.create()`/`create_batch()` (per-row saves +
  post_generation = hours). Seeders use `Factory.build(...)` in chunks of
  10k + `bulk_create(batch_size=1000)` (~21k rows/s measured).
- Do not pass `ignore_conflicts=True` — it disables RETURNING, and the
  db-generated uuidv7 pks are needed to bulk-create child rows.
- Child rows fan out per parent with variance via
  `fan_out(parents, per_parent=(lo, hi), build_child, rng)` — a new child
  model (e.g. addresses) adds one seeder step declaring its ratio, and must
  replicate whatever its factory's post_generation would have done (bulk
  path bypasses hooks).
- Seeded rows carry the `@seed.example.com` email domain: `--wipe` deletes
  exactly those; sequence offsets off the existing domain count so re-runs
  append without conflicts. `--seed N` = deterministic (`random.seed` +
  `fake.reseed` + `factory.random.reseed_random` — the last one seeds
  factory_boy's own RNG, which `FuzzyChoice` draws from).
- Spread timestamps for realism with `QuerySet.update()` (bypasses
  auto_now/auto_now_add); remember `F()` reads pre-update values, so
  copying a freshly-randomized column needs a second UPDATE.
