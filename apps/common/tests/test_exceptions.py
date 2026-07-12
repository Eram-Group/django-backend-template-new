"""ApplicationError machine-readable codes (WP4)."""

from apps.common.exceptions import ApplicationError


class SampleNotFoundError(ApplicationError):
    status_code = 404


class ExplicitCodeError(ApplicationError):
    code = "custom_code"


def test_code_is_derived_from_the_class_name() -> None:
    assert SampleNotFoundError.code == "sample_not_found"


def test_explicit_code_wins_over_derivation() -> None:
    assert ExplicitCodeError.code == "custom_code"


def test_base_class_code() -> None:
    assert ApplicationError.code == "application_error"


def test_derivation_survives_subclassing_an_explicit_code() -> None:
    class ChildOfExplicitError(ExplicitCodeError):
        pass

    assert ChildOfExplicitError.code == "child_of_explicit"
