export COMPOSE_FILE := "docker-compose.local.yml"

# Interim recipe set (postgres + mailpit only) - full rewrite lands in G09.

# Default command to list all available commands.
default:
    @just --list

# up: Start up containers (postgres + mailpit).
up:
    @echo "Starting up containers..."
    @docker compose up -d --remove-orphans

# down: Stop containers.
down:
    @echo "Stopping containers..."
    @docker compose down

# prune: Remove containers and their volumes.
prune *args:
    @echo "Killing containers and removing volumes..."
    @docker compose down -v {{args}}

# logs: View container logs
logs *args:
    @docker compose logs -f {{args}}
