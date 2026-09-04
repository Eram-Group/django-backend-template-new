# Working on the template

This repository is a [Copier](https://copier.readthedocs.io) template, not a
runnable project. `copier.yml` holds the questions; `presets/` the answer
sets CI generates. Branding is not a question: projects edit
`config/branding.py` after generation.

```bash
uvx copier copy gh:Eram-Group/backend-template my-app      # a new project
uvx copier copy --defaults --data-file presets/postgis.yml . build/postgis   # a preset, locally
uvx copier update                                            # in a project: pull template changes
```

## How rendering works

- Only files ending in `.jinja` are rendered (and lose the suffix). Every
  other file is copied byte-for-byte - `uv.lock`, Django templates, the
  `${{ }}` in workflows are all safe there.
- Directory *names* are always rendered:
  `apps/{% if database == 'postgis' %}zones{% endif %}/` renders to
  `apps/zones/` or disappears entirely.
- A `.jinja` file that contains Jinja-looking text that is NOT for Copier
  (`{{ site_name }}` in a Django template, `${{ github.sha }}` in a workflow)
  must wrap it in `{% raw %}…{% endraw %}`. The five deploy/guard workflows
  are wrapped whole: they carry no variables and are suffixed only so GitHub
  does not run them in this repository.
- Conditional lines use `{%- if database == "postgis" %}` … `{%- endif %}` on
  their own lines: the leading `-` eats the newline before the tag, so the
  output has no blank lines where a block was skipped.
- `.copier-answers.yml` is written from
  `{{ _copier_conf.answers_file }}.jinja`; `copier update` needs it.

## Which files are `.jinja`

| File | Why |
|---|---|
| `infra/backend_infra/config.py` | app name, repo, domain, emails; GDAL/GEOS keys |
| `config/settings/base.py` | SITE_NAME; gis app, engine, zones app + sidebar |
| `config/env.py`, `.env.example` | GDAL/GEOS keys |
| `pyproject.toml` | zones in the import-linter contracts |
| `apps/common/tests/factories_registry.py` | Zone factory |
| `Dockerfile`, `compose.yaml`, `justfile`, `.github/workflows/ci.yml` | GIS libraries / postgis image |
| `.github/dependabot.yml` | timezone |
| `README.md`, `docs/*.md` | project name, PostGIS paragraphs |
| `.pre-commit-config.yaml`, other workflows | suffix only (see above) |

Everything else - including all of `apps/` - is plain code. Keep it that way:
a knob that needs Jinja in application code is a sign the feature wants to be
its own app (as zones is), not a conditional.

## Developing

1. Generate a preset into `build/<preset>` (gitignored), `cd` there,
   `git init && git add -A && cp .env.example .env`, then `just gates` and
   `just infra-gates` - exactly what `.github/workflows/template.yml` runs
   for every preset on every push.
   Copier clones a git source at its HEAD commit, so uncommitted template
   edits are NOT rendered from `.` - commit first, or generate from a plain
   copy of the working tree (`rsync -a --exclude .git --exclude build . /tmp/tpl`
   then `copier copy ... /tmp/tpl build/<preset>`).
2. Make the change in the generated tree, watch the gates, then back-port it
   into the template source (`.jinja` or plain file). Never commit `build/`.
3. Add a preset when a new answer combination needs its own proof; the
   `branded` preset exists so every identity substitution is exercised
   off-default.
4. The template repo's own pre-commit config is hygiene only (`.jinja` files
   are skipped) and lives at `.pre-commit-config.template.yaml` - install it
   with `uv run pre-commit install -c .pre-commit-config.template.yaml`. The
   real lint runs on the generated output. `_exclude` matches destination
   paths, so a template-only file may never share a name with a rendered
   one (the template README is `.github/README.md` for the same reason).

## Releasing

`copier update` resolves the template version from git tags (PEP 440, e.g.
`v1.2.0`), so tag `main` after merging: `git tag v1.x.y && git push --tags`.
Projects pin the tag they were generated from in `.copier-answers.yml`
(`_commit`) and move forward with `uvx copier update`, which re-renders the
new version and 3-way-merges the project's own changes; conflicts land
inline as git markers.
