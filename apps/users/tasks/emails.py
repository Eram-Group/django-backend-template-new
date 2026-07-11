from django.core.mail import send_mail
from django.tasks import task
from django.utils import translation
from django.utils.translation import gettext as _


@task()
def send_welcome_email(user_id: str) -> None:
    """Welcome email, rendered in the user's language (no request in a worker)."""
    from apps.users.models import User

    user = User.objects.get(pk=user_id)
    with translation.override(user.language):
        subject = _("Welcome!")
        body = _("Welcome aboard, %(name)s!") % {"name": user.name or user.email}
    send_mail(subject, body, None, [user.email])
