"""Allauth account adapter.

Project rule (no signals): allauth reports completed signups through this
adapter's save_user - which calls the service - instead of the
user_signed_up signal. The social adapter (G04 social-providers) must do
the same after it saves the user (it calls this one with commit=False).

The full passwordless hooks (generate_login_code, is_login_by_code_required,
skip-OTP-for-social) land in G04 (adapters-passwordless).
"""

from typing import Any

from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest

from apps.users.models import User
from apps.users.services import user_post_signup
from config.env import env


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Signup kill-switch driven by the ACCOUNT_ALLOW_REGISTRATION env flag."""
        return env.ACCOUNT_ALLOW_REGISTRATION

    def save_user(
        self,
        request: HttpRequest,
        user: User,
        form: Any,
        commit: bool = True,
    ) -> User:
        """Run post-signup side effects once the user row actually exists."""
        saved: User = super().save_user(request, user, form, commit=commit)
        if commit:
            # Social signups reach here with commit=False (no pk yet); the
            # social adapter triggers user_post_signup after ITS save (G04).
            user_post_signup(user=saved)
        return saved
