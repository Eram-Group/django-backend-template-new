"""ApplicationError machine-readable codes."""

from apps.common.exceptions import ApplicationError


class SampleNotFoundError(ApplicationError):
    status_code = 404


class RenamedError(ApplicationError):
    code = "custom_code"  # ignored: the class name is the code


def test_code_is_derived_from_the_class_name() -> None:
    assert SampleNotFoundError.code == "sample_not_found"


def test_base_class_code() -> None:
    assert ApplicationError.code == "application"


def test_the_class_name_is_the_only_road_to_a_code() -> None:
    assert RenamedError.code == "renamed"

    class ChildOfRenamedError(RenamedError):
        pass

    assert ChildOfRenamedError.code == "child_of_renamed"


def test_extra_is_carried_verbatim() -> None:
    error = SampleNotFoundError("gone", extra={"fields": {"pk": ["unknown"]}})
    assert error.message == "gone"
    assert error.extra == {"fields": {"pk": ["unknown"]}}
    assert SampleNotFoundError("gone").extra == {}
