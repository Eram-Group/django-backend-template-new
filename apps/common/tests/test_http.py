"""The outbound HTTP kernel: retry policies, error taxonomy, logging."""

from collections.abc import Iterator

import httpx
import pytest
import respx
import stamina

from apps.common.http import OutboundStatusError
from apps.common.http import OutboundTransportError
from apps.common.http import request_json

URL = "https://api.example.com/send"


@pytest.fixture
def _full_retries() -> Iterator[None]:
    """Re-enable the kernel's real attempt count (still without sleeping)."""
    stamina.set_testing(True, attempts=3)
    yield
    stamina.set_testing(True, attempts=1)


@respx.mock
def test_success_returns_the_response() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    response = request_json(service="svc", method="POST", url=URL, json={"a": 1})

    assert response.json() == {"ok": True}
    assert route.call_count == 1


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_transient_retries_5xx_then_succeeds() -> None:
    route = respx.post(URL)
    route.side_effect = [httpx.Response(500), httpx.Response(200, json={"ok": True})]

    response = request_json(service="svc", method="POST", url=URL)

    assert response.status_code == 200
    assert route.call_count == 2


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_transient_gives_up_after_attempts() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(503))

    with pytest.raises(OutboundStatusError) as excinfo:
        request_json(service="svc", method="POST", url=URL)

    assert excinfo.value.status_code == 503
    assert excinfo.value.service == "svc"
    assert route.call_count == 3


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_transient_does_not_retry_4xx() -> None:
    route = respx.post(URL).mock(
        return_value=httpx.Response(400, json={"error": "bad"})
    )

    with pytest.raises(OutboundStatusError) as excinfo:
        request_json(service="svc", method="POST", url=URL)

    assert excinfo.value.status_code == 400
    assert route.call_count == 1


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_connect_only_retries_connect_errors() -> None:
    route = respx.post(URL)
    route.side_effect = [
        httpx.ConnectError("refused"),
        httpx.Response(200, json={"ok": True}),
    ]

    response = request_json(service="svc", method="POST", url=URL, retry="connect-only")

    assert response.status_code == 200
    assert route.call_count == 2


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_connect_only_does_not_retry_5xx() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(500))

    with pytest.raises(OutboundStatusError):
        request_json(service="svc", method="POST", url=URL, retry="connect-only")

    assert route.call_count == 1


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_connect_only_does_not_retry_read_timeouts() -> None:
    """A timed-out POST may have reached the provider - never resend it."""
    route = respx.post(URL).mock(side_effect=httpx.ReadTimeout("slow"))

    with pytest.raises(OutboundTransportError):
        request_json(service="svc", method="POST", url=URL, retry="connect-only")

    assert route.call_count == 1


@pytest.mark.usefixtures("_full_retries")
@respx.mock
def test_none_sends_exactly_once() -> None:
    route = respx.post(URL).mock(return_value=httpx.Response(502))

    with pytest.raises(OutboundStatusError):
        request_json(service="svc", method="POST", url=URL, retry="none")

    assert route.call_count == 1


@respx.mock
def test_transport_failure_raises_transport_error() -> None:
    respx.get(URL).mock(side_effect=httpx.ConnectError("dns"))

    with pytest.raises(OutboundTransportError) as excinfo:
        request_json(service="svc", method="GET", url=URL)

    assert excinfo.value.service == "svc"
    assert excinfo.value.status_code is None


@respx.mock
def test_status_error_body_is_truncated() -> None:
    respx.post(URL).mock(return_value=httpx.Response(400, text="x" * 2_000))

    with pytest.raises(OutboundStatusError) as excinfo:
        request_json(service="svc", method="POST", url=URL)

    assert excinfo.value.body is not None
    assert len(excinfo.value.body) == 500
