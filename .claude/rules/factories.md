---
paths:
  - "apps/**/tests/factories.py"
  - "apps/common/tests/fake.py"
  - "apps/common/tests/factories_registry.py"
  - "apps/common/tests/test_factory_coverage.py"
  - "apps/common/management/commands/seed_db.py"
---

# Factories & seed data

Stack: factory_boy 3.3+ (structure) + mimesis 19+ (values). Faker is only a
transitive dependency of factory_boy — never import it.

## Writing a factory

- One factory per concrete model, in `apps/<app>/tests/factories.py`,
  subclassing `DjangoModelFactory[Model]` (the generic subscript is
  supported at runtime and keeps mypy strict happy).
- `class Meta` must set `skip_postgeneration_save = True` (3.3+ behavior:
  avoids the deprecated second save after post_generation) and
  `django_get_or_create = [...]` on the natural key (e.g. email).
- Structure comes from factory_boy declarations (`Sequence`,
  `LazyAttribute`, `LazyFunction`, `SubFactory`, `Trait` under
  `class Params`); every fake VALUE comes from `apps/common/tests/fake.py`
  (mimesis `Locale.AR_SA` / `Locale.EN` providers — Arabic users get Arabic
  values). Add new value helpers there, never instantiate mimesis/Faker in
  a factory.
- Factories satisfy runtime invariants, not just DB constraints. UserFactory
  is the reference: verified primary `EmailAddress` via `post_generation`
  (code-login pends on verify_email without it), `password = "!"`
  (passwordless invariant), locale-matched `name`, weighted `language`.
- `post_generation` hooks must no-op when `create` is False —
  `Factory.build()` must never touch the database.
- Register the factory in
  `apps/common/tests/factories_registry.py::FACTORIES` in the SAME change
  that adds the model — the coverage gate
  (`apps/common/tests/test_factory_coverage.py`) fails the suite otherwise.
  The registry is an explicit dict; no auto-discovery, no swallowed errors.

## Bulk seeding (seed_db)

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
  `fake.reseed`).
- Spread timestamps for realism with `QuerySet.update()` (bypasses
  auto_now/auto_now_add); remember `F()` reads pre-update values, so
  copying a freshly-randomized column needs a second UPDATE.
