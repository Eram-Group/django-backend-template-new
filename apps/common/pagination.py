import base64
import uuid
from typing import Any

from django.db.models import QuerySet
from django.http import HttpRequest
from django.utils.translation import gettext as _
from ninja import Field
from ninja import Schema
from ninja.pagination import PaginationBase

from apps.common.exceptions import ApplicationError

# Page size when the client sends no ``limit``, and the ceiling it can ask for.
DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


class InvalidCursorError(ApplicationError):
    """The client sent a cursor this API never issued."""


class CursorPagination(PaginationBase):
    """Cursor pagination over the UUIDv7 pk (time-ordered): newest first.

    The project-wide default - every list endpoint declares it:
        @router.get(...)
        @paginate(CursorPagination)
    """

    class Input(Schema):
        cursor: str | None = None
        limit: int = Field(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE)

    class Output(Schema):
        items: list[Any]
        next_cursor: str | None

    items_attribute = "items"

    def paginate_queryset(
        self,
        queryset: QuerySet[Any],
        pagination: Input,
        request: HttpRequest,
        **params: Any,
    ) -> dict[str, Any]:
        ordered = queryset.order_by("-pk")
        if pagination.cursor is not None:
            ordered = ordered.filter(pk__lt=_decode_cursor(pagination.cursor))
        page = list(ordered[: pagination.limit + 1])
        has_next = len(page) > pagination.limit
        items = page[: pagination.limit]
        return {
            "items": items,
            "next_cursor": _encode_cursor(items[-1].pk) if has_next else None,
        }


def _encode_cursor(pk: uuid.UUID) -> str:
    # 16 bytes always pad to "==" - drop it; the decoder restores it.
    return base64.urlsafe_b64encode(pk.bytes).decode().rstrip("=")


def _decode_cursor(cursor: str) -> uuid.UUID:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        return uuid.UUID(bytes=base64.urlsafe_b64decode(padded.encode()))
    except ValueError as exc:  # binascii.Error is a ValueError
        raise InvalidCursorError(_("Invalid pagination cursor.")) from exc
