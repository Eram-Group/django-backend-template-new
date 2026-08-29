"""Money-on-the-wire helpers."""

from decimal import Decimal

import pytest

from apps.payments.gateways.base import MINOR_UNITS
from apps.payments.gateways.base import to_minor_units


@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (Decimal("12.34"), 1234),
        (Decimal("0.01"), 1),
        (Decimal(50), 5000),
        (Decimal("99999999.99"), 9999999999),
    ],
)
def test_to_minor_units(amount: Decimal, expected: int) -> None:
    assert to_minor_units(amount=amount) == expected


def test_minor_units_is_two_for_every_shipped_currency() -> None:
    """SAR and EGP both carry two decimals - one constant, no table."""
    assert MINOR_UNITS == 2


def test_to_minor_units_rejects_sub_cent_precision() -> None:
    with pytest.raises(ValueError, match="decimal places"):
        to_minor_units(amount=Decimal("1.001"))


def test_to_minor_units_never_floats() -> None:
    # The classic float bug: 19.99 * 100 == 1998.9999...
    assert to_minor_units(amount=Decimal("19.99")) == 1999
