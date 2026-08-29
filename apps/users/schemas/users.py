import uuid
from datetime import datetime

from ninja import Schema

from apps.users.constants import Language
from apps.users.models import User


class UserSummary(Schema):
    id: uuid.UUID
    email: str
    name: str


class UserDetail(UserSummary):
    phone: str  # E164 ("+9665..."), empty string when unset
    language: Language
    created_at: datetime

    @staticmethod
    def resolve_phone(obj: User) -> str:
        # PhoneNumberField yields a PhoneNumber object, not a str.
        return str(obj.phone)


class UserUpdateIn(Schema):
    """PATCH input - handlers wrap it in PatchDict[UserUpdateIn], which
    auto-optionalizes every field and delivers only the keys the client
    actually sent (absent != null)."""

    name: str
    phone: str  # E164 with country code; "" clears it
    language: Language
