"""Allauth account adapter.

G02 ships only the signup kill-switch; the full passwordless hooks
(generate_login_code, is_login_by_code_required, skip-OTP-for-social)
land in G04 (adapters-passwordless).
"""

from allauth.account.adapter import DefaultAccountAdapter
from django.http import HttpRequest

from config.env import env


class AccountAdapter(DefaultAccountAdapter):
    def is_open_for_signup(self, request: HttpRequest) -> bool:
        """Signup kill-switch driven by the ACCOUNT_ALLOW_REGISTRATION env flag."""
        return env.ACCOUNT_ALLOW_REGISTRATION
