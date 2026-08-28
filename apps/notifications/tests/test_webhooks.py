"""WhatsApp status webhook: handshake, signature, status ingestion."""

import hashlib
import hmac
import json
from typing import Any

import pytest
from django.test import Client
from pydantic import SecretStr
from structlog.testing import capture_logs

from apps.notifications.constants import Channel
from apps.notifications.constants import DeliveryStatus
from apps.notifications.tests.factories import NotificationDeliveryFactory

pytestmark = pytest.mark.django_db

WEBHOOK = "/api/v1/notifications/webhooks/whatsapp"
SECRET = "app-secret"


@pytest.fixture
def _whatsapp_webhook_creds(settings: Any) -> None:
    settings.WHATSAPP_APP_SECRET = SecretStr(SECRET)
    settings.WHATSAPP_WEBHOOK_VERIFY_TOKEN = "verify-me"


def _signed(payload: dict[str, Any]) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    return body, signature


def _meta_payload(
    message_id: str, status: str, errors: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    status_item: dict[str, Any] = {"id": message_id, "status": status}
    if errors:
        status_item["errors"] = errors
    return {"entry": [{"changes": [{"value": {"statuses": [status_item]}}]}]}


def _delivery(status: DeliveryStatus = DeliveryStatus.SENT) -> Any:
    return NotificationDeliveryFactory.create(
        channel=Channel.WHATSAPP,
        status=status,
        provider="whatsapp",
        provider_message_id="wamid.hook.1",
    )


# --- GET handshake ------------------------------------------------------------


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_handshake_echoes_challenge_on_token_match(client: Client) -> None:
    response = client.get(
        WEBHOOK,
        {
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "12345",
        },
    )

    assert response.status_code == 200
    assert response.content == b"12345"


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_handshake_rejects_wrong_token(client: Client) -> None:
    response = client.get(WEBHOOK, {"hub.verify_token": "nope"})

    assert response.status_code == 400


def test_handshake_fails_closed_when_unconfigured(client: Client) -> None:
    response = client.get(WEBHOOK, {"hub.verify_token": ""})

    assert response.status_code == 400


# --- POST statuses ------------------------------------------------------------


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_status_report_updates_the_delivery(client: Client) -> None:
    delivery = _delivery()
    body, signature = _signed(_meta_payload("wamid.hook.1", "delivered"))

    response = client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.DELIVERED


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_failed_status_records_the_error_title(client: Client) -> None:
    delivery = _delivery()
    body, signature = _signed(
        _meta_payload(
            "wamid.hook.1",
            "failed",
            errors=[{"code": 131050, "title": "User blocked the business"}],
        )
    )

    client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.FAILED
    assert delivery.detail == "User blocked the business"


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_out_of_order_report_is_a_no_op(client: Client) -> None:
    delivery = _delivery(status=DeliveryStatus.READ)
    body, signature = _signed(_meta_payload("wamid.hook.1", "delivered"))

    response = client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    assert response.status_code == 200  # acked, ignored
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.READ


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_unknown_message_id_still_acks_200(client: Client) -> None:
    body, signature = _signed(_meta_payload("wamid.unknown", "delivered"))

    response = client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    assert response.status_code == 200


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_bad_signature_is_rejected_and_changes_nothing(client: Client) -> None:
    delivery = _delivery()
    body, _signature = _signed(_meta_payload("wamid.hook.1", "delivered"))

    response = client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": "sha256=deadbeef"},
    )

    assert response.status_code == 400
    delivery.refresh_from_db()
    assert delivery.status == DeliveryStatus.SENT


def test_unconfigured_secret_fails_closed(client: Client) -> None:
    body, signature = _signed(_meta_payload("wamid.hook.1", "delivered"))

    response = client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    assert response.status_code == 400


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_invalid_json_is_rejected(client: Client) -> None:
    body = b"not-json"
    signature = "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()

    response = client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )

    assert response.status_code == 400


# --- input shapes that must never 500 (Meta retries 5xx for days) -------------


def _post(client: Client, body: bytes, signature: str) -> Any:
    return client.post(
        WEBHOOK,
        body,
        content_type="application/json",
        headers={"X-Hub-Signature-256": signature},
    )


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_non_ascii_signature_header_is_a_400_not_a_500(client: Client) -> None:
    """hmac.compare_digest raises TypeError on non-ASCII str - the header is
    attacker-controlled, so the compare runs on bytes."""
    body, _signature = _signed(_meta_payload("wamid.hook.1", "delivered"))

    response = _post(client, body, "sha256=\xe9")

    assert response.status_code == 400


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
@pytest.mark.parametrize("raw", [b"[]", b"null", b'"text"', b"42"])
def test_valid_json_that_is_not_an_object_is_rejected(
    client: Client, raw: bytes
) -> None:
    signature = "sha256=" + hmac.new(SECRET.encode(), raw, hashlib.sha256).hexdigest()

    response = _post(client, raw, signature)

    assert response.status_code == 400


def _statuses(*items: Any) -> dict[str, Any]:
    return {"entry": [{"changes": [{"value": {"statuses": list(items)}}]}]}


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
@pytest.mark.parametrize(
    "payload",
    [
        {"entry": None},
        {"entry": "x"},
        {"entry": [None, 1, "x"]},
        {"entry": [{"changes": None}]},
        {"entry": [{"changes": [{"value": None}]}]},
        {"entry": [{"changes": [{"value": {"statuses": "nope"}}]}]},
        _statuses(None, 1),
        _statuses({"status": ["x"]}),
        _statuses({"status": "sent", "id": 5}),
        _statuses({"status": "failed", "id": "wamid.hook.1", "errors": {"code": 1}}),
    ],
)
def test_signed_payloads_of_the_wrong_shape_ack_200(
    client: Client, payload: dict[str, Any]
) -> None:
    _delivery()
    body, signature = _signed(payload)

    response = _post(client, body, signature)

    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_rejection_is_logged_at_error_with_a_fixed_reason(client: Client) -> None:
    """A wrong app secret on a fresh deployment rejects every callback - that
    must reach Sentry, and the log must carry our reason, never the header."""
    body, _signature = _signed(_meta_payload("wamid.hook.1", "delivered"))

    with capture_logs() as logs:
        response = _post(client, body, "sha256=deadbeef")

    assert response.status_code == 400
    rejected = [log for log in logs if log["event"] == "notification_webhook_rejected"]
    assert len(rejected) == 1
    assert rejected[0]["log_level"] == "error"
    assert rejected[0]["reason"] == "signature mismatch"
    assert "deadbeef" not in str(rejected[0])


@pytest.mark.usefixtures("_whatsapp_webhook_creds")
def test_accepted_webhook_logs_no_rejection(client: Client) -> None:
    body, signature = _signed(_meta_payload("wamid.unknown", "delivered"))

    with capture_logs() as logs:
        _post(client, body, signature)

    assert not [log for log in logs if log["event"] == "notification_webhook_rejected"]
