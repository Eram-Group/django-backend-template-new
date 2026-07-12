from django.tasks import task
from django.utils import translation
from django.utils.translation import gettext as _

from apps.common.emails import email_send


@task()
def send_welcome_email(user_id: str) -> None:
    """Welcome email, rendered in the user's language (no request in a worker)."""
    from apps.users.models import User

    user = User.objects.get(pk=user_id)
    with translation.override(user.language):
        email_send(
            subject=_("Welcome!"),
            recipient_list=[user.email],
            template_name="emails/welcome.html",
            context={"name": user.name or user.email},
        )
