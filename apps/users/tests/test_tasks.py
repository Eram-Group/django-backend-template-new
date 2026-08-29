import pytest
from django.core.mail import EmailMessage
from django.core.mail import EmailMultiAlternatives

from apps.users.tasks import send_welcome_email
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_send_welcome_email_addresses_the_user_by_name(
    mailoutbox: list[EmailMessage],
) -> None:
    user = UserFactory.create(name="Omar")

    send_welcome_email.enqueue(str(user.pk))  # ImmediateBackend runs it now

    assert len(mailoutbox) == 1
    message = mailoutbox[0]
    assert message.to == [user.email]
    assert "Omar" in message.body


def test_send_welcome_email_is_branded_multipart_html(
    mailoutbox: list[EmailMessage],
) -> None:
    user = UserFactory.create(name="Multipart")

    send_welcome_email.enqueue(str(user.pk))

    message = mailoutbox[0]
    assert isinstance(message, EmailMultiAlternatives)
    assert message.alternatives, "welcome email must carry an HTML alternative"
    html_body, mimetype = message.alternatives[0]
    assert mimetype == "text/html"
    assert "Multipart" in str(html_body)


def test_send_welcome_email_renders_rtl_for_arabic_users(
    mailoutbox: list[EmailMessage],
) -> None:
    user = UserFactory.create(language="ar")

    send_welcome_email.enqueue(str(user.pk))

    message = mailoutbox[0]
    assert isinstance(message, EmailMultiAlternatives)
    html_body = str(message.alternatives[0][0])
    assert 'dir="rtl"' in html_body
    assert 'lang="ar"' in html_body
