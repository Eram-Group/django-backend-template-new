"""The admin's Content Security Policy: strict, but runnable by unfold."""

import pytest
from django.test import Client

pytestmark = pytest.mark.django_db


def test_admin_csp_allows_alpine_but_no_foreign_or_inline_scripts(
    client: Client,
) -> None:
    """unfold's Alpine.js needs 'unsafe-eval' (it compiles the expressions
    in its HTML with the Function constructor); everything else stays
    locked: scripts only from this origin or nonce-carrying inline tags."""
    policy = client.get("/admin/login/").headers["Content-Security-Policy"]
    script_src = next(
        part.strip()
        for part in policy.split(";")
        if part.strip().startswith("script-src")
    )

    assert "'self'" in script_src
    assert "'unsafe-eval'" in script_src
    assert "'unsafe-inline'" not in script_src
    assert "frame-ancestors 'none'" in policy
