import uuid
from datetime import datetime

from ninja import Schema

from apps.users.constants import Language


class UserSummary(Schema):
    id: uuid.UUID
    email: str
    name: str


class UserDetail(UserSummary):
    language: Language
    created_at: datetime


class UserUpdateIn(Schema):
    """All-optional PATCH input - apply with .dict(exclude_unset=True)."""

    name: str | None = None
    language: Language | None = None
