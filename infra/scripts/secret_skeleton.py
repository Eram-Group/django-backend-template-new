"""Print the JSON skeleton of the ``<env>/<app>`` Secrets Manager secret.

Every key must exist (ECS refuses to start a task whose secret key is
missing); an empty string means "unset" to the app (env_ignore_empty).

    uv run python scripts/secret_skeleton.py dev
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run from infra/

from backend_infra.config import ENV_SECRET
from backend_infra.config import ENVIRONMENTS

env_name = sys.argv[1] if len(sys.argv) > 1 else "dev"
cfg = ENVIRONMENTS[env_name]  # type: ignore[index]
keys = sorted(ENV_SECRET - ({"DATABASE_URL"} if cfg.database == "dedicated" else set()))
sys.stdout.write(json.dumps(dict.fromkeys(keys, ""), indent=2) + "\n")
