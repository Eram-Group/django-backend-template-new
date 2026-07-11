"""Helpers for the admin basics gate."""

from typing import Any

from django.contrib import admin
from django.contrib.admin.options import ModelAdmin
from django.db.models import Model
from django.forms import Form
from django.forms.widgets import CheckboxInput
from django.forms.widgets import FileInput
from django.forms.widgets import MultiWidget
from django.urls import reverse


def registered_models() -> list[type[Model]]:
    return sorted(admin.site._registry, key=lambda m: str(m._meta.label))


def admin_for(model: type[Model]) -> ModelAdmin[Any]:
    return admin.site._registry[model]


def is_local(model: type[Model]) -> bool:
    return model.__module__.startswith("apps.")


def page_url(model: type[Model], page: str, *args: Any) -> str:
    opts = model._meta
    return reverse(f"admin:{opts.app_label}_{opts.model_name}_{page}", args=args)


def form_post_data(response: Any) -> dict[str, Any]:
    """Re-encode a rendered change form (incl. inline formsets) as POST data."""
    data: dict[str, Any] = {}
    _encode_form(response.context["adminform"].form, data)
    if "inline_admin_formsets" in response.context:
        for inline in response.context["inline_admin_formsets"]:
            _encode_form(inline.formset.management_form, data)
            for form in inline.formset.forms:
                _encode_form(form, data)
    return data


def _encode_form(form: Form, data: dict[str, Any]) -> None:
    for bound in form:
        widget = bound.field.widget
        value = bound.value()
        if isinstance(widget, CheckboxInput):
            if value:
                data[bound.html_name] = "on"
        elif isinstance(widget, FileInput):
            continue  # unchanged file uploads post nothing
        elif isinstance(widget, MultiWidget):
            for i, part in enumerate(widget.decompress(value) or []):
                data[f"{bound.html_name}_{i}"] = "" if part is None else str(part)
        elif isinstance(value, list | tuple):
            data[bound.html_name] = [str(item) for item in value]
        elif value is None:
            data[bound.html_name] = ""
        else:
            formatted = widget.format_value(value)
            data[bound.html_name] = "" if formatted is None else formatted
