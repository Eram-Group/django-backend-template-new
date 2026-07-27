"""SMS providers: success predicates, bulk groups, routing, backend switch."""

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.notifications.clients.sms import sms_send
from apps.notifications.clients.sms import sms_send_many
from apps.notifications.clients.sms.backends import outbox
from apps.notifications.clients.sms.base import SmsNotConfiguredError
from apps.notifications.clients.sms.base import SmsProviderError
from apps.notifications.clients.sms.oursms import OurSmsBackend
from apps.notifications.clients.sms.routing import RoutingSmsBackend
from apps.notifications.clients.sms.smsmisr import SmsMisrBackend

OURSMS_URL = "https://api.oursms.com/msgs/sms"
SMSMISR_URL = "https://smsmisr.com/api/SMS/"


@pytest.fixture
def _oursms_creds(settings: Any) -> None:
    settings.OURSMS_API_KEY = SecretStr("key")
    settings.OURSMS_SENDER = "Brand"


@pytest.fixture
def _smsmisr_creds(settings: Any) -> None:
    settings.SMSMISR_USERNAME = "user"
    settings.SMSMISR_PASSWORD = SecretStr("pass")
    settings.SMSMISR_SENDER = "Brand"


# --- OurSMS -------------------------------------------------------------------


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_sends_bulk_bearer_payload() -> None:
    """One POST carries the whole dests group - the bulk contract."""
    route = respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 3, "rejected": 0})
    )

    OurSmsBackend().send_many(
        to=["+966501234567", "+966501234568", "+966501234569"], body="hello"
    )

    assert route.call_count == 1
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer key"
    payload = json.loads(request.content)
    assert payload["dests"] == ["+966501234567", "+966501234568", "+966501234569"]
    assert payload["src"] == "Brand"


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_partial_acceptance_fails_the_group() -> None:
    """Counts carry no per-number detail - partial acceptance is a failure."""
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 2, "rejected": 1})
    )

    with pytest.raises(SmsProviderError):
        OurSmsBackend().send_many(
            to=["+966501234567", "+966501234568", "+966501234569"], body="hello"
        )


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_error_in_200_body_fails() -> None:
    """A 2xx carrying a rejection is a FAILURE (the old template's bug)."""
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 0, "rejected": 1})
    )

    with pytest.raises(SmsProviderError):
        OurSmsBackend().send_many(to=["+966501234567"], body="hello")


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_unknown_response_shape_fails() -> None:
    respx.post(OURSMS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    with pytest.raises(SmsProviderError):
        OurSmsBackend().send_many(to=["+966501234567"], body="hello")


def test_oursms_without_creds_is_loud() -> None:
    with pytest.raises(SmsNotConfiguredError):
        OurSmsBackend().send_many(to=["+966501234567"], body="hello")


# --- SMSMisr ------------------------------------------------------------------


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_english_body_uses_language_1() -> None:
    route = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    SmsMisrBackend().send_many(to=["+201001234567"], body="hello")

    payload = json.loads(route.calls.last.request.content)
    assert payload["language"] == "1"
    assert payload["mobile"] == "201001234567"  # no leading +
    assert payload["environment"] == "2"  # test mode outside production


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_arabic_body_uses_language_2() -> None:
    route = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    SmsMisrBackend().send_many(to=["+201001234567"], body="مرحبا")

    payload = json.loads(route.calls.last.request.content)
    assert payload["language"] == "2"


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_loops_one_post_per_number() -> None:
    """No bulk endpoint - send_many is a per-number loop."""
    route = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    SmsMisrBackend().send_many(to=["+201001234567", "+201001234568"], body="hello")

    assert route.call_count == 2


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_error_code_fails() -> None:
    respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1906"})  # no credit
    )

    with pytest.raises(SmsProviderError, match="1906"):
        SmsMisrBackend().send_many(to=["+201001234567"], body="hello")


def test_smsmisr_without_creds_is_loud() -> None:
    with pytest.raises(SmsNotConfiguredError):
        SmsMisrBackend().send_many(to=["+201001234567"], body="hello")


# --- Routing ------------------------------------------------------------------


@pytest.mark.usefixtures("_oursms_creds", "_smsmisr_creds")
@respx.mock
def test_routing_groups_numbers_per_provider() -> None:
    """One provider call per country group: SA numbers share one bulk POST."""
    oursms = respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 3, "rejected": 0})
    )
    smsmisr = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    RoutingSmsBackend().send_many(
        to=[
            "+966501234567",  # SA
            "+201001234567",  # EG
            "+966501234568",  # SA
            "+14155550123",  # other -> default (OurSMS)
        ],
        body="hi",
    )

    assert oursms.call_count == 1
    assert smsmisr.call_count == 1
    oursms_payload = json.loads(oursms.calls.last.request.content)
    assert oursms_payload["dests"] == [
        "+966501234567",
        "+966501234568",
        "+14155550123",
    ]


# --- The settings switch --------------------------------------------------------


def test_sms_send_many_uses_locmem_backend_in_tests() -> None:
    sms_send_many(to=["+966501234567", "+201001234567"], body="hello")

    assert [entry.to for entry in outbox] == ["+966501234567", "+201001234567"]
    assert all(entry.body == "hello" for entry in outbox)


def test_sms_send_single_wraps_send_many() -> None:
    sms_send(to="+966501234567", body="hello")

    assert len(outbox) == 1
    assert outbox[0].to == "+966501234567"
