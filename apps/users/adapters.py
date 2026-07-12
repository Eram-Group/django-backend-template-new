"""Allauth account adapter.

Project rule (no signals): allauth reports completed signups through this
adapter's save_user - which calls the service - instead of the
user_signed_up signal. The social adapter (G04 social-providers) must do
the same after it saves the user (it calls this one with commit=False).

Skip-OTP-for-social needs no override here: ACCOUNT_LOGIN_BY_CODE_REQUIRED
uses allauth's method-list semantics in settings (verified in 65.18 source).
"""

from typing import Any

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
        """Signup kill-switch driven by the ACCOUNT_ALLOW_REGISTRATION env flag."""
        return env.ACCOUNT_ALLOW_REGISTRATION

    def generate_login_code(self) -> str:
        """6-digit numeric login codes (allauth default is dashed alphanumeric)."""
        return get_random_string(length=CODE_LENGTH, allowed_chars=CODE_ALPHABET)

    def generate_email_verification_code(self) -> str:
        """6-digit numeric email verification codes."""
        return get_random_string(length=CODE_LENGTH, allowed_chars=CODE_ALPHABET)

    def save_user(
        self,
        request: HttpRequest,
        user: User,
        form: Any,
        commit: bool = True,
    ) -> User:
        """Run post-signup side effects once the user row actually exists."""
        # First-touchpoint localization: the welcome email renders in
        # user.language, which defaults to Arabic - capture the client's
        # Accept-Language (constrained to LANGUAGES by Django) before the
        # post-signup task enqueues.
        user.language = get_language_from_request(request)
        saved: User = super().save_user(request, user, form, commit=commit)
        if commit:
            # Social signups reach here with commit=False (no pk yet); the
            # social adapter triggers user_post_signup after ITS save (G04).
            user_post_signup(user=saved)
        return saved
