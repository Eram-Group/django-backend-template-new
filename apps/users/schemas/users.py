import uuid
from datetime import datetime

from ninja import Schema

from apps.users.constants import Language


class UserSummary(Schema):
    id: uuid.UUID
    email: str
    name: str


class UserDetail(UserSummary):
    phone: str  # E164 ("+9665..."), empty string when unset
    language: Language
    created_at: datetime

    @staticmethod
    def resolve_phone(obj: object) -> str:
        # PhoneNumberField yields a PhoneNumber object, not a str.
        return str(getattr(obj, "phone", "") or "")


class UserUpdateIn(Schema):
    """All-optional PATCH input - apply with .dict(exclude_unset=True)."""

    name: str | None = None
    phone: str | None = None  # E164 with country code; "" clears it
    language: Language | None = None
