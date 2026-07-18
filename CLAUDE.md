# Project rules

PLAN.md is the authoritative design document; TODO.json is the task tracker;
docs/ARCHITECTURE.md explains the conventions in depth. Topic rules live in
`.claude/rules/` and auto-load when their files are touched.

## Architecture rules (digest)

- Layering per app, enforced by import-linter:
  `apis | admin | management → services → tasks → selectors → models`.
  Apps are independent; `apps.common` imports no domain app.
- Cross-app access: one-way only, each direction recorded in `pyproject.toml`
  `ignore_imports`. Importing another app's **model** is allowed only for
  passive ownership (FK target, read-only — e.g. `User` from payments).
  Anything with behavior or invariants goes through the owning app's
  **service** (`notification_send`, `wallet_apply`) — never mutate another
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
  `CreateIn` and `UpdateIn` are separate classes; PATCH applies
  `.dict(exclude_unset=True)`. Lists paginate with
  `apps/common/pagination.CursorPagination`.
- **Env discipline**: `config/env.py` is the only `os.environ` reader; every
  field required, no code defaults; a new field updates `.env.example` in
  the same change (test_env_contract enforces the sync).
- Tasks (`django.tasks`): bodies take pk strings, are idempotent, use only
  models/selectors; services enqueue via `transaction.on_commit`.
- Admin: subclass `apps.common.admin.BaseModelAdmin`; declare
  `can_add/can_change/can_delete`; field rules via `FieldPermissions`;
  scaffold new packages with `manage.py generate_dashboard <app> <Model>`.
- Arabic-first: `gettext_lazy` for strings; user-facing emails render in
  `user.language`; content models use per-app `translation.py`.
- Models inherit `apps.common.models.BaseModel` (db-generated uuidv7 pk);
  register a factory for every new model (coverage gate).

## Daily commands

- `just bootstrap` (one-time) · `just run` (dev server) · `just up`
  (prod-parity compose stack)
- `just test` · `just lint` · `just fmt` · `just typecheck` ·
  `uv run lint-imports`
- `just manage <cmd>` · `just migrate` · `just makemigrations` ·
  `just seed [scale]` · `just worker` · `just superuser` ·
  `just check-deploy` · `just messages` (i18n catalogs) ·
  `just pr` / `just branch <name>` (PR flow) · `just update` / `just outdated`
- Before finishing any change, run the gates: `ruff check`, `mypy`,
  `pytest`, `lint-imports` — all must be green (coverage floor 80%,
  warnings are errors).

## Factories & seed data (essentials)

Full rule: `.claude/rules/factories.md` (loads when factory/seed files are
in play).

- Every concrete model ships a factory (`apps/<app>/tests/factories.py`)
  registered in `apps/common/tests/factories_registry.py` - the coverage
  gate fails the suite otherwise.
- factory_boy = structure, mimesis via `apps/common/tests/fake.py` = values
  (locale-aware ar/en). Never import Faker.
- Related objects via `RelatedFactory` (dotted-path string if the class
  import would be circular) — never a hand-rolled post_generation hook;
  exemplar: `UserFactory.wallet`.
- Seed data: `just seed` / `manage.py seed_db --scale 0..1` (log curve,
  1.0 = 1M users; local-only; `--wipe` removes the `@seed.example.com` rows).
