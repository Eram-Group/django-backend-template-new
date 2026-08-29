"""Signup fields beyond allauth's own (ACCOUNT_SIGNUP_FORM_CLASS).

allauth merges this form into its signup form - headless included, so the
JSON signup input requires ``name`` next to ``email``.
"""

from django import forms
from django.http import HttpRequest

from apps.users.models import User


class SignupForm(forms.Form):
    name = forms.CharField(max_length=255)

    def signup(self, request: HttpRequest, user: User) -> None:
        """allauth insists on this hook; it runs after the adapter's
        save_user, which already persisted every field through user_create."""
