"""SMS providers: success predicates, routing, and the backend switch."""

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.notifications.clients.sms import sms_send
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
def test_oursms_sends_bearer_payload() -> None:
    route = respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 0})
    )

    OurSmsBackend().send(to="+966501234567", body="hello")

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer key"
    payload = json.loads(request.content)
    assert payload["dests"] == ["+966501234567"]
    assert payload["src"] == "Brand"


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_error_in_200_body_fails() -> None:
    """A 2xx carrying a rejection is a FAILURE (the old template's bug)."""
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 0, "rejected": 1})
    )

    with pytest.raises(SmsProviderError):
        OurSmsBackend().send(to="+966501234567", body="hello")


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_unknown_response_shape_fails() -> None:
    respx.post(OURSMS_URL).mock(return_value=httpx.Response(200, json={"ok": True}))

    with pytest.raises(SmsProviderError):
        OurSmsBackend().send(to="+966501234567", body="hello")


def test_oursms_without_creds_is_loud() -> None:
    with pytest.raises(SmsNotConfiguredError):
        OurSmsBackend().send(to="+966501234567", body="hello")


# --- SMSMisr ------------------------------------------------------------------


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_english_body_uses_language_1() -> None:
    route = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    SmsMisrBackend().send(to="+201001234567", body="hello")

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

    SmsMisrBackend().send(to="+201001234567", body="مرحبا")

    payload = json.loads(route.calls.last.request.content)
    assert payload["language"] == "2"


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_error_code_fails() -> None:
    respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1906"})  # no credit
    )

    with pytest.raises(SmsProviderError, match="1906"):
        SmsMisrBackend().send(to="+201001234567", body="hello")


def test_smsmisr_without_creds_is_loud() -> None:
    with pytest.raises(SmsNotConfiguredError):
        SmsMisrBackend().send(to="+201001234567", body="hello")


# --- Routing ------------------------------------------------------------------


@pytest.mark.usefixtures("_oursms_creds", "_smsmisr_creds")
@respx.mock
def test_routing_picks_provider_by_country() -> None:
    oursms = respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 0})
    )
    smsmisr = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    RoutingSmsBackend().send(to="+966501234567", body="hi")  # SA
    RoutingSmsBackend().send(to="+201001234567", body="hi")  # EG
    RoutingSmsBackend().send(to="+14155550123", body="hi")  # other -> default

    assert oursms.call_count == 2
    assert smsmisr.call_count == 1


# --- The settings switch --------------------------------------------------------


def test_sms_send_uses_locmem_backend_in_tests() -> None:
    sms_send(to="+966501234567", body="hello")

    assert len(outbox) == 1
    assert outbox[0].to == "+966501234567"
    assert outbox[0].body == "hello"
