"""Allauth account adapter.

Project rule (no signals): allauth reports completed signups through this
adapter's save_user - which calls the service - instead of the
user_signed_up signal. The social adapter (G04 social-providers) must do
the same after it saves the user (it calls this one with commit=False).

Skip-OTP-for-social needs no override here: ACCOUNT_LOGIN_BY_CODE_REQUIRED
uses allauth's method-list semantics in settings (verified in 65.18 source).
"""

from typing import Any

from allauth.account import app_settings as account_settings
from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest
from django.utils.crypto import get_random_string
from django.utils.translation import get_language_from_request

from apps.users.models import User
from apps.users.services import user_post_signup
from config.env import env

CODE_LENGTH = 6
CODE_ALPHABET = "0123456789"


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        return env.ACCOUNT_ALLOW_REGISTRATION

    def generate_login_code(self) -> str:
        return get_random_string(length=CODE_LENGTH, allowed_chars=CODE_ALPHABET)

    def generate_email_verification_code(self) -> str:
        return get_random_string(length=CODE_LENGTH, allowed_chars=CODE_ALPHABET)

    def send_mail(self, template_prefix: str, email: str, context: Any) -> None:
        if template_prefix == "account/email/login_code":
            context = {
                **context,
                "code_ttl_minutes": account_settings.LOGIN_BY_CODE_TIMEOUT // 60,
            }
        super().send_mail(template_prefix, email, context)

    def save_user(
        self,
        request: HttpRequest,
        user: User,
        form: Any,
        commit: bool = True,
    ) -> User:

        user.language = get_language_from_request(request)
        saved: User = super().save_user(request, user, form, commit=commit)
        if commit:
            user_post_signup(user=saved)
        return saved
