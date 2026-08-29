"""Resource names in one place - CD variables and the runbook quote these."""

from backend_infra.config import AppConfig
from backend_infra.config import EnvConfig

# Express Mode requires the primary container to carry this exact name; the
# CD workflows render task definitions by it too. Never rename.
MAIN_CONTAINER = "Main"


def web_family(app: AppConfig, env: EnvConfig) -> str:
    return f"{app.name}-{env.name}-web"


def worker_family(app: AppConfig, env: EnvConfig) -> str:
    return f"{app.name}-{env.name}-worker"


def app_secret_name(app: AppConfig, env: EnvConfig) -> str:
    return f"{env.name}/{app.name}"


def bucket_name(app: AppConfig, env: EnvConfig) -> str:
    return f"eram-{app.name}-{env.name}"


def log_group(app: AppConfig, env: EnvConfig, role: str) -> str:
    return f"/aws/ecs/{app.name}-{env.name}-{role}"
