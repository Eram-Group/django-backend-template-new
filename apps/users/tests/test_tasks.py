import pytest
from django.core import mail

from apps.users.tasks import send_welcome_email
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_send_welcome_email_addresses_the_user_by_name() -> None:
    user = UserFactory.create(name="Omar")

    send_welcome_email.enqueue(str(user.pk))  # ImmediateBackend runs it now

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [user.email]
    assert "Omar" in message.body


def test_send_welcome_email_falls_back_to_email_without_name() -> None:
    user = UserFactory.create(name="")

    send_welcome_email.enqueue(str(user.pk))

    assert user.email in mail.outbox[0].body
