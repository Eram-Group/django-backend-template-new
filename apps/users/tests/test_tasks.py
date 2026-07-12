import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives

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


def test_send_welcome_email_is_branded_multipart_html() -> None:
    user = UserFactory.create(name="Multipart")

    send_welcome_email.enqueue(str(user.pk))

    message = mail.outbox[0]
    assert isinstance(message, EmailMultiAlternatives)
    assert message.alternatives, "welcome email must carry an HTML alternative"
    html_body, mimetype = message.alternatives[0]
    assert mimetype == "text/html"
    assert "Multipart" in str(html_body)


def test_send_welcome_email_renders_rtl_for_arabic_users() -> None:
    user = UserFactory.create(language="ar")

    send_welcome_email.enqueue(str(user.pk))

    message = mail.outbox[0]
    assert isinstance(message, EmailMultiAlternatives)
    html_body = str(message.alternatives[0][0])
    assert 'dir="rtl"' in html_body
    assert 'lang="ar"' in html_body
