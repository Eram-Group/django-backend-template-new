"""Locale-aware fake values (mimesis) - the ONLY value source for factories.

factory_boy provides structure (relations, sequences, traits); every fake
VALUE comes from here so realism and locales stay in one place. Seed data
respects the user's language: Arabic users get Arabic names.
"""

from mimesis import Address
from mimesis import Person
from mimesis.locales import Locale

from apps.users.constants import Language

_PERSONS: dict[str, Person] = {
    Language.ARABIC: Person(Locale.AR_SA),
    Language.ENGLISH: Person(Locale.EN),
}
_ADDRESSES: dict[str, Address] = {
    Language.ARABIC: Address(Locale.AR_SA),
    Language.ENGLISH: Address(Locale.EN),
}


def full_name(language: str) -> str:
    return _PERSONS[language].full_name()


def city(language: str) -> str:
    return _ADDRESSES[language].city()


def reseed(seed: int) -> None:
    """Make subsequent values deterministic (used by seed_db --seed)."""
    for person in _PERSONS.values():
        person.reseed(seed)
    for address in _ADDRESSES.values():
        address.reseed(seed)
