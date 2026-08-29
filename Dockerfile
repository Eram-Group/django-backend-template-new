# syntax=docker/dockerfile:1
# The single ECS artifact: web (this CMD) and worker (command: manage.py
# db_worker) run the same image. The release step (migrate + createcachetable
# + seed_notification_config + collectstatic) is a one-off ECS task - NEVER
# the entrypoint, NEVER here.

# --- Build stage: resolve the locked, prod-only environment -------------------
FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

# gettext: compile .po -> .mo at build time (runtime stays gettext-free)
RUN apt-get update && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Phase 1: dependencies only - cached until uv.lock/pyproject.toml change
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --no-install-project

# Phase 2: project source
COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# Bake the Arabic catalog (locale/ar) - a missing catalog fails the build.
RUN .venv/bin/django-admin compilemessages --ignore .venv

# --- Runtime stage: slim, non-root -------------------------------------------
# Debian codename pinned to match the builder base - the .venv (and any
# compiled wheels) must run against the same libc it was built on.
FROM python:3.14-slim-bookworm AS runtime

ENV PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    DJANGO_SETTINGS_MODULE=config.settings.production

RUN groupadd --system app && useradd --system --gid app --home-dir /app app

WORKDIR /app
COPY --from=builder --chown=app:app /app /app
# COPY --chown covers contents only; /app itself must be writable (media dir).
RUN mkdir -p /app/media && chown app:app /app /app/media

USER app
EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--config", "gunicorn.conf.py"]
