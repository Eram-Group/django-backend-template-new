"""Unit tests for the field-permission helpers (no DB needed)."""

from apps.common.admin import expand_translation_shadows


def test_expands_to_existing_shadow_columns() -> None:
    fields = expand_translation_shadows(
        fields=("name",), model_field_names={"name", "name_ar", "name_en", "other"}
    )
    assert fields == ("name", "name_ar", "name_en")


def test_ignores_absent_shadows() -> None:
    assert expand_translation_shadows(
        fields=("email",), model_field_names={"email"}
    ) == ("email",)


def test_keeps_order_and_partial_shadows() -> None:
    fields = expand_translation_shadows(
        fields=("title", "name"),
        model_field_names={"title", "title_ar", "name", "name_en"},
    )
    assert fields == ("title", "title_ar", "name", "name_en")
