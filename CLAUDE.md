# Project rules

PLAN.md is the authoritative design document; TODO.json is the task tracker.
(This file grows with more rule sections as the scaffold matures - G11.)

Topic rules live in `.claude/rules/` and auto-load when their files are
touched.

## Factories & seed data (essentials)

Full rule: `.claude/rules/factories.md` (loads when factory/seed files are
in play).

- Every concrete model ships a factory (`apps/<app>/tests/factories.py`)
  registered in `apps/common/tests/factories_registry.py` - the coverage
  gate fails the suite otherwise.
- factory_boy = structure, mimesis via `apps/common/tests/fake.py` = values
  (locale-aware ar/en). Never import Faker.
- Seed data: `just seed` / `manage.py seed_db --scale 0..1` (log curve,
  1.0 = 1M users; local-only; `--wipe` removes the `@seed.example.com` rows).
