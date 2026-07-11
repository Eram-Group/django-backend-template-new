"""Factory-coverage gate: every concrete model in apps/ has a registered factory."""

from django.apps import apps

from apps.common.tests.factories_registry import FACTORIES


def test_every_local_model_has_a_registered_factory() -> None:
    local_models = [
        model
        for model in apps.get_models()
        if model.__module__.startswith("apps.") and not model._meta.proxy
    ]
    missing = [model._meta.label for model in local_models if model not in FACTORIES]
    assert not missing, (
        "Concrete models without a registered factory: "
        f"{', '.join(missing)}. Register them in "
        "apps.common.tests.factories_registry.FACTORIES."
    )
