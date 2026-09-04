"""manage.py release: the one release step every deploy path runs."""

from typing import Any

import pytest
from django.core.management import call_command


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, Any]]]:
    recorded: list[tuple[str, dict[str, Any]]] = []
    from apps.common.management.commands import release

    monkeypatch.setattr(
        release, "call_command", lambda name, **kw: recorded.append((name, kw))
    )
    return recorded


def test_release_runs_checks_migrations_cache_table_and_static(
    calls: list[tuple[str, dict[str, Any]]],
) -> None:
    call_command("release")

    assert [name for name, _ in calls] == [
        "check",
        "migrate",
        "createcachetable",
        "collectstatic",
    ]
    assert calls[0][1] == {"deploy": True, "fail_level": "WARNING"}
    assert calls[1][1] == {"interactive": False}


def test_skip_static_is_for_the_smoke_image_only(
    calls: list[tuple[str, dict[str, Any]]],
) -> None:
    call_command("release", skip_static=True)

    assert [name for name, _ in calls] == ["check", "migrate", "createcachetable"]
