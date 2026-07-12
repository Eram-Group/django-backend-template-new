"""Script validators for bilingual (_ar/_en) content fields."""

import pytest
from django.core.exceptions import ValidationError

from apps.common.validators import validate_arabic_text
from apps.common.validators import validate_english_text


def test_arabic_validator_accepts_arabic_with_digits_and_punctuation() -> None:
    validate_arabic_text("مرحبا بالعالم! 123")


def test_arabic_validator_rejects_latin_letters() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_arabic_text("مرحبا hello")
    assert excinfo.value.code == "arabic_text_only"


def test_english_validator_accepts_english_with_digits_and_punctuation() -> None:
    validate_english_text("Hello, world! 123")


def test_english_validator_rejects_arabic_letters() -> None:
    with pytest.raises(ValidationError) as excinfo:
        validate_english_text("Hello مرحبا")
    assert excinfo.value.code == "english_text_only"


def test_validators_are_deconstructible_for_migrations() -> None:
    for validator in (validate_arabic_text, validate_english_text):
        path, _args, _kwargs = validator.deconstruct()
        assert path == "django.core.validators.RegexValidator"
