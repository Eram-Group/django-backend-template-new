# Project rules

PLAN.md is the authoritative design document; TODO.json is the task tracker.
(This file grows with more rule sections as the scaffold matures - G11.)

## Factories & seed data

- Every concrete model in `apps/` ships a factory in
  `apps/<app>/tests/factories.py` AND registers it in
  `apps/common/tests/factories_registry.py`. The factory-coverage gate
  (`apps/common/tests/test_factory_coverage.py`) fails the suite for any
  missing entry - register the factory in the same change that adds a model.
- factory_boy provides STRUCTURE (Sequence, LazyAttribute, SubFactory,
  Trait, post_generation); every fake VALUE comes from
  `apps/common/tests/fake.py` (mimesis, locale-aware: Arabic users get
  ar-sa values, English users en). Never import Faker or mimesis directly
  in a factory or seeder.
- Factories must satisfy runtime invariants, not just DB constraints: a
  User factory always creates a verified primary `EmailAddress` (code-login
  pends on verify_email otherwise) and regular users stay passwordless
  (`password = "!"`).
- Always set `skip_postgeneration_save = True`; use `django_get_or_create`
  for natural keys (e.g. email).
- Bulk seeding NEVER calls `Factory.create()`/`create_batch()` (per-row
  saves + post_generation take hours at scale). Seeders in
  `apps/common/management/commands/seed_db.py` use `Factory.build(...)` +
  chunked `bulk_create(batch_size=1000)`; related rows are built per parent
  with variance via `fan_out(parents, per_parent=(lo, hi), ...)` - a new
  child model (e.g. addresses) adds a seeder step declaring its ratio.
- `manage.py seed_db --scale 0..1` (log curve: 0 -> 10 users, 0.5 -> ~3.2k,
  1.0 -> 1,000,000) refuses to run unless `ENVIRONMENT=local`. Seeded rows
  carry the `@seed.example.com` email domain; `--wipe` deletes exactly
  those; `--seed N` makes runs deterministic. Day-to-day: `just seed`
  (defaults to scale 0.3 ≈ 316 users).
