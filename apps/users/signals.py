"""Signal receivers - thin: one call into the service layer, nothing else."""

from typing import Any

from allauth.account.signals import user_signed_up
from django.dispatch import receiver
from django.http import HttpRequest

from apps.users.models import User
from apps.users.services import user_post_signup


@receiver(user_signed_up)
def handle_user_signed_up(
    request: HttpRequest,
    user: User,
    **kwargs: Any,
) -> None:
    user_post_signup(user=user)
