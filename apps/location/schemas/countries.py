import uuid

from ninja import Field
from ninja import Schema

from apps.location.models import Country


class CountrySummary(Schema):
    """One field per name: ``Accept-Language`` picks the language (never
    name_ar/name_en on the wire)."""

    id: uuid.UUID
    code: str
    name: str
    dial_code: str
    phone_example: str
    max_phone_length: int = Field(
        description=(
            "Longest national number (digits, no dial code) the country's "
            "dial plan allows - safe as the input's maxlength."
        )
    )
    currency: str
    flag_url: str | None

    @staticmethod
    def resolve_flag_url(obj: Country) -> str | None:
        return obj.flag.url if obj.flag else None
