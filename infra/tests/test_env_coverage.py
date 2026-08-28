"""The IaC env-var ownership sets must mirror ``.env.example`` exactly."""

import re
from pathlib import Path

from backend_infra.config import ENV_FROM_CD
from backend_infra.config import ENV_FROM_STACK
from backend_infra.config import ENV_PLAIN
from backend_infra.config import ENV_SECRET
from backend_infra.config import ENVIRONMENTS

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


def _example_keys() -> set[str]:
    return set(
        re.findall(r"^([A-Z][A-Z0-9_]*)=", ENV_EXAMPLE.read_text(), flags=re.MULTILINE)
    )


def test_ownership_sets_are_disjoint() -> None:
    groups = [ENV_PLAIN, ENV_FROM_STACK, ENV_SECRET, ENV_FROM_CD]
    assert sum(len(g) for g in groups) == len(frozenset().union(*groups))


def test_every_env_key_has_exactly_one_owner() -> None:
    owned = ENV_PLAIN | ENV_FROM_STACK | ENV_SECRET | ENV_FROM_CD
    assert owned == _example_keys()


def test_each_environment_sets_every_plain_key() -> None:
    for cfg in ENVIRONMENTS.values():
        assert set(cfg.plain_env) == ENV_PLAIN, cfg.name
        assert cfg.plain_env["ENVIRONMENT"] == cfg.name
        assert cfg.plain_env["TASKS_IMMEDIATE"] == "false"
