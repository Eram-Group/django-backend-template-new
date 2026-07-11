# Example app — the starting point for every new app

Copy this app to start a new domain (one app = one bounded context):

1. `cp -r apps/example apps/<name>`, then rename `example`/`Example` throughout.
2. `apps.py`: `<Name>Config`, `name = "apps.<name>"`, `label = "<name>"`.
3. Add `"apps.<name>"` to `INSTALLED_APPS` (config/settings/base.py).
4. Flesh out the layers (uncomment the samples), then
   `uv run manage.py makemigrations <name>`.
5. Mount the router in `config/api/v1.py`: `api.add_router("/<name>s", router)`.
6. Generate the admin package: `uv run manage.py generate_dashboard <name> <Model>`.
7. Register the factory in the registry (apps/common/tests/).
8. Append the app to the import-linter contracts in pyproject.toml
   (layers containers + independence modules).

## Rules that shape every file here

- Layering, imports point down only:
  `apis / admin / management` → `services / tasks` → `selectors` → `models`.
  Leaf modules (`constants.py`, `exceptions.py`, `types.py`) are importable
  by every layer.
- No signals: services call services; third-party boundaries use adapter hooks.
- Cross-app access ONLY via the other app's `services`/`selectors`.
- Models inherit `apps.common.models.BaseModel` (UUIDv7 pk, timestamps).
- Services take keyword-only args, run `full_clean()` before save, and raise
  `ApplicationError` subclasses.
- Schemas: `Summary` / `Detail(Summary)` outputs; separate `CreateIn` /
  `UpdateIn` (all-optional, PATCH via `exclude_unset`); never ModelSchema.
- List endpoints declare `apps.common.pagination.CursorPagination`.
