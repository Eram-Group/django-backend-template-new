"""SMS providers: success predicates, bulk groups, routing, the swapped seam."""

import json
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from apps.common.http import OutboundTransportError
from apps.notifications.clients.sms import sms_send
from apps.notifications.clients.sms import sms_send_many
from apps.notifications.clients.sms.base import SmsNotConfiguredError
from apps.notifications.clients.sms.base import SmsProviderError
from apps.notifications.clients.sms.oursms import OurSmsBackend
from apps.notifications.clients.sms.routing import RoutingSmsBackend
from apps.notifications.clients.sms.routing import provider_for
from apps.notifications.clients.sms.smsmisr import SmsMisrBackend
from apps.notifications.tests.locmem import sms_outbox

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
        return_value=httpx.Response(200, json={"accepted": 2, "rejected": 0})
    )
    smsmisr = respx.post(SMSMISR_URL).mock(
        return_value=httpx.Response(200, json={"code": "1901"})
    )

    RoutingSmsBackend().send_many(
        to=[
            "+966501234567",  # SA
            "+201001234567",  # EG
            "+966501234568",  # SA
        ],
        body="hi",
    )

    assert oursms.call_count == 1
    assert smsmisr.call_count == 1
    oursms_payload = json.loads(oursms.calls.last.request.content)
    assert oursms_payload["dests"] == ["+966501234567", "+966501234568"]


@pytest.mark.usefixtures("_oursms_creds", "_smsmisr_creds")
@respx.mock
def test_routing_refuses_an_unmapped_region_before_any_send() -> None:
    """No default provider: a number outside the registry fails the group
    up front - nothing is silently handed to an international-capable
    provider nobody chose for it."""
    oursms = respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 0})
    )

    with pytest.raises(SmsProviderError, match="'US'") as excinfo:
        RoutingSmsBackend().send_many(to=["+966501234567", "+14155550123"], body="hi")

    assert excinfo.value.provider == "routing"
    assert excinfo.value.sent == ()
    assert oursms.call_count == 0


def test_provider_for_rejects_an_unparseable_number() -> None:
    with pytest.raises(SmsProviderError, match="unparseable"):
        provider_for("not-a-number")


# --- The swapped seam -------------------------------------------------------------


def test_sms_send_many_goes_through_the_swapped_seam() -> None:
    sms_send_many(to=["+966501234567", "+201001234567"], body="hello")

    assert [entry.to for entry in sms_outbox] == ["+966501234567", "+201001234567"]
    assert all(entry.body == "hello" for entry in sms_outbox)


def test_sms_send_single_wraps_send_many() -> None:
    sms_send(to="+966501234567", body="hello")

    assert len(sms_outbox) == 1
    assert sms_outbox[0].to == "+966501234567"


# --- partial progress is reported, never lost -----------------------------------


@pytest.mark.usefixtures("_smsmisr_creds")
@respx.mock
def test_smsmisr_reports_the_numbers_sent_before_the_first_rejection() -> None:
    respx.post(SMSMISR_URL).mock(
        side_effect=[
            httpx.Response(200, json={"code": "1901"}),
            httpx.Response(200, json={"code": "1905"}),  # bad mobile - loop stops
            httpx.Response(200, json={"code": "1901"}),
        ]
    )

    with pytest.raises(SmsProviderError) as excinfo:
        SmsMisrBackend().send_many(
            to=["+201001234567", "+201001234568", "+201001234569"], body="hello"
        )

    assert excinfo.value.sent == ("+201001234567",)


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_oursms_rejected_bulk_group_reports_nothing_sent() -> None:
    """Counts carry no per-number detail: a rejected bulk group fails whole."""
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 1})
    )

    with pytest.raises(SmsProviderError) as excinfo:
        OurSmsBackend().send_many(to=["+966501234567", "+966501234568"], body="hi")

    assert excinfo.value.sent == ()


@pytest.mark.usefixtures("_oursms_creds", "_smsmisr_creds")
@respx.mock
def test_routing_carries_the_earlier_provider_group_as_sent() -> None:
    """OurSMS accepted its group, then SMSMisr rejected: the SA numbers are
    with the provider and must not be reported as failed."""
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 2, "rejected": 0})
    )
    respx.post(SMSMISR_URL).mock(
        side_effect=[
            httpx.Response(200, json={"code": "1901"}),
            httpx.Response(200, json={"code": "1906"}),
        ]
    )

    with pytest.raises(SmsProviderError) as excinfo:
        RoutingSmsBackend().send_many(
            to=["+966501234567", "+966501234568", "+201001234567", "+201001234568"],
            body="hi",
        )

    assert excinfo.value.sent == ("+966501234567", "+966501234568", "+201001234567")
    assert excinfo.value.provider == "smsmisr"


@pytest.mark.usefixtures("_oursms_creds", "_smsmisr_creds")
@respx.mock
def test_routing_turns_a_later_transport_failure_into_a_rejection_with_progress() -> (
    None
):
    """A 5xx/transport failure AFTER another provider already accepted numbers
    surfaces as SmsProviderError carrying them; escaping as systemic would
    leave those rows PROCESSING for the sweep to re-send."""
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 0})
    )
    respx.post(SMSMISR_URL).mock(side_effect=httpx.ConnectError("down"))

    with pytest.raises(SmsProviderError) as excinfo:
        RoutingSmsBackend().send_many(to=["+966501234567", "+201001234567"], body="hi")

    assert excinfo.value.sent == ("+966501234567",)


@pytest.mark.usefixtures("_oursms_creds", "_smsmisr_creds")
@respx.mock
def test_routing_keeps_a_failure_before_any_send_systemic() -> None:
    """Nothing went through yet - the task should fail loudly as before."""
    respx.post(SMSMISR_URL).mock(side_effect=httpx.ConnectError("down"))
    oursms = respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 0})
    )

    with pytest.raises(OutboundTransportError):
        RoutingSmsBackend().send_many(to=["+201001234567", "+966501234567"], body="hi")

    assert oursms.call_count == 0  # the loop stopped at the first provider


@pytest.mark.usefixtures("_oursms_creds")
@respx.mock
def test_routing_reports_an_unconfigured_later_provider_with_progress() -> None:
    respx.post(OURSMS_URL).mock(
        return_value=httpx.Response(200, json={"accepted": 1, "rejected": 0})
    )

    with pytest.raises(SmsProviderError) as excinfo:  # SMSMisr creds unset
        RoutingSmsBackend().send_many(to=["+966501234567", "+201001234567"], body="hi")

    assert excinfo.value.sent == ("+966501234567",)
    assert "SMSMISR" in excinfo.value.detail
