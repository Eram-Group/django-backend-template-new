"""WhatsApp status webhook: handshake, signature, status ingestion."""

import hashlib
import hmac
import json
from typing import Any

import pytest
from django.test import Client
from pydantic import SecretStr

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
