# Daily-driver recipes. Dev flow: Django runs on the host (local settings,
# debug toolbar); postgres+mailpit run in compose. `just up` brings the full
# prod-parity stack (web+worker containers on production settings).

# Recipes see .env values (createsuperuser reads DJANGO_SUPERUSER_* from env).
set dotenv-load := true

# Default: list all recipes.
default:
    @just --list

# One-time setup: deps, .env, git hooks, infra, release step.
bootstrap:
    uv sync
    cp -n .env.example .env || true
    uv run pre-commit install --hook-type pre-commit --hook-type pre-push
    docker compose up -d --wait postgres mailpit
    uv run manage.py migrate
    uv run manage.py createcachetable

# Host dev server (local settings, debug toolbar, inline tasks).
run *args:
    uv run manage.py runserver {{ args }}

# Full prod-parity stack: web + worker + postgres + mailpit.
up:
    docker compose up --build -d --wait

# Stop containers (keep volumes).
stop:
    docker compose stop

# Container logs, e.g. `just logs web` / `just logs -f worker`.
logs *args:
    docker compose logs {{ args }}

# manage.py passthrough, e.g. `just manage createsuperuser`.
manage +args:
    uv run manage.py {{ args }}

# Test suite (parallel).
test *args:
    uv run pytest -n auto {{ args }}

# Static checks: ruff + architecture contracts.
lint:
    uv run ruff check .
    uv run ruff format --check .
    uv run lint-imports

# Auto-fix + format.
fmt:
    uv run ruff check . --fix
    uv run ruff format .

# Strict mypy.
typecheck:
    uv run mypy

# Apply migrations + ensure the cache table exists.
migrate:
    uv run manage.py migrate
    uv run manage.py createcachetable

# makemigrations passthrough, e.g. `just makemigrations users`.
makemigrations *args:
    uv run manage.py makemigrations {{ args }}

# Django shell.
shell *args:
    uv run manage.py shell {{ args }}

# Drain the DB task queue on the host (`just worker --batch` = drain & exit).
worker *args:
    TASKS_IMMEDIATE=false uv run manage.py db_worker {{ args }}

# Idempotent superuser from DJANGO_SUPERUSER_* (.env locally, Secrets in AWS).
superuser:
    uv run manage.py createsu

# Seed fake data: scale 0..1 is logarithmic (0 = 10 users, 1 = 1,000,000).
seed scale="0.3":
    uv run manage.py seed_db --scale {{scale}}

# Destroy the database volume and rebuild from zero.
[confirm("Drop ALL local containers + volumes and re-migrate? (y/N)")]
db-reset:
    docker compose down -v
    docker compose up -d --wait postgres mailpit
    uv run manage.py migrate
    uv run manage.py createcachetable

# Shell inside the production image, e.g. `just bash` or `just bash -c 'id'`.
bash *args:
    docker compose run --rm web bash {{ args }}

# Extract + compile ar/en translation catalogs (compile = local verification
# only; images compile at build once .po files exist).
messages:
    uv run manage.py makemessages -l ar -l en --ignore .venv --ignore staticfiles --ignore Gawdat_Django_Template --ignore sample_project
    uv run manage.py compilemessages --ignore .venv --ignore Gawdat_Django_Template --ignore sample_project

# Push the current branch (or move stray commits off main) and open a PR.
pr *flags:
    ./scripts/git-pr.sh {{ flags }}

# Start a fresh branch off up-to-date main.
branch name:
    git checkout main
    git pull
    git checkout -b {{ name }}

# Bump the lockfile to the latest compatible versions (Renovate is the
# scheduled path; this is the local escape hatch). Run `just test` after.
update:
    uv lock --upgrade

# List dependencies with newer versions available.
outdated:
    uv tree --outdated --depth 1

# Remove caches and local build artifacts.
clean:
    rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov staticfiles
    find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +

# Django deploy checklist against deployed-mode settings (dummy prod values).
check-deploy:
    DJANGO_SETTINGS_MODULE=config.settings.production \
    ENVIRONMENT=production \
    SECRET_KEY=check-deploy-only-nZfK3vQ8wLxT1pYbR6mJc9GhD4sE7uAiO2 \
    DJANGO_SUPERUSER_PASSWORD=check-deploy-only \
    DATABASE_URL=postgres://user:pass@db.invalid:5432/app \
    AWS_STORAGE_BUCKET_NAME=bucket AWS_S3_REGION_NAME=me-south-1 \
    AWS_SES_REGION=me-south-1 SENTRY_DSN=https://key@sentry.invalid/1 \
    uv run manage.py check --deploy
