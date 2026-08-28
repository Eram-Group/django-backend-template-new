"""CloudFront origin is appended to every CSP directive when deployed."""

from django.utils.csp import CSP

from config.settings.base import csp_with_origin


def test_csp_with_origin_extends_every_directive() -> None:
    csp: dict[str, list[str]] = {
        "default-src": [CSP.SELF],
        "script-src": [CSP.SELF, CSP.NONCE],
    }
    result = csp_with_origin(csp, "https://d123.cloudfront.net")
    assert result == {
        "default-src": [CSP.SELF, "https://d123.cloudfront.net"],
        "script-src": [CSP.SELF, CSP.NONCE, "https://d123.cloudfront.net"],
    }
    assert csp["script-src"] == [CSP.SELF, CSP.NONCE]  # input untouched
