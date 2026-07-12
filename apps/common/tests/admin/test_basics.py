"""Admin basics gate - registry-driven, zero per-admin tests.

Every admin in ``admin.site._registry`` (current AND future) is exercised:
changelist, search, every filter APPLIED, per-column sorting both ways,
add/change/delete/history pages, an unchanged GET->re-POST save round-trip,
CSV export with rows, autocomplete endpoints, data coverage, framework
enforcement, sidebar permission consistency, and the hidden-field tamper
check. Permission-aware: restricted pages must 403, not error.
"""

from typing import Any
from typing import cast

import pytest
from django.conf import settings
from django.contrib.admin.utils import quote
from django.db.models import Model
from django.test import Client
from django.test import RequestFactory
from django.urls import reverse
from import_export.admin import ExportMixin

from apps.common.admin import AdminContext
from apps.common.admin import BaseModelAdmin
from apps.common.admin import BaseStackedInline
from apps.common.admin import BaseTabularInline
from apps.common.admin import expand_translation_shadows
from apps.common.tests.admin.support import admin_for
from apps.common.tests.admin.support import form_post_data
from apps.common.tests.admin.support import is_local
from apps.common.tests.admin.support import page_url
from apps.common.tests.admin.support import registered_models

pytestmark = pytest.mark.django_db

ALL_MODELS = registered_models()
LOCAL_MODELS = [model for model in ALL_MODELS if is_local(model)]
EXPORTABLE_MODELS = [
    model for model in ALL_MODELS if isinstance(admin_for(model), ExportMixin)
]

parametrize_all = pytest.mark.parametrize(
    "model", ALL_MODELS, ids=[str(m._meta.label) for m in ALL_MODELS]
)
parametrize_local = pytest.mark.parametrize(
    "model", LOCAL_MODELS, ids=[str(m._meta.label) for m in LOCAL_MODELS]
)
parametrize_exportable = pytest.mark.parametrize(
    "model", EXPORTABLE_MODELS, ids=[str(m._meta.label) for m in EXPORTABLE_MODELS]
)


def _request(user: Any, path: str = "/admin/") -> Any:
    request = RequestFactory().get(path)
    request.user = user
    return request


def _first(model: type[Model]) -> Model | None:
    return model._default_manager.first()


def _obj_or_skip(model: type[Model]) -> Model:
    obj = _first(model)
    if obj is None:
        # Local models without rows fail test_data_coverage; third-party
        # models (task results, login attempts...) legitimately start empty.
        pytest.skip(f"no rows for {model._meta.label}")
    return obj


# --- index ----------------------------------------------------------------


def test_admin_index(su_client: Client, staff_client: Client) -> None:
    """The page every staff user lands on - and the coverage anchor for the
    deferred unfold insights/KPI dashboard."""
    assert su_client.get(reverse("admin:index")).status_code == 200
    assert staff_client.get(reverse("admin:index")).status_code == 200


# --- changelist ---------------------------------------------------------


@parametrize_all
def test_changelist(su_client: Client, model: type[Model]) -> None:
    response = su_client.get(page_url(model, "changelist"))
    assert response.status_code == 200


@parametrize_all
def test_search(su_client: Client, model: type[Model]) -> None:
    if not admin_for(model).search_fields:
        pytest.skip("no search_fields")
    response = su_client.get(page_url(model, "changelist"), {"q": "gate probe"})
    assert response.status_code == 200


@parametrize_all
def test_every_filter_applied(su_client: Client, model: type[Model]) -> None:
    """Follow each filter choice link - the filtered queryset must execute."""
    response = su_client.get(page_url(model, "changelist"))
    changelist = response.context["cl"]
    if not changelist.filter_specs:
        pytest.skip("no list_filter")
    for spec in changelist.filter_specs:
        for choice in list(spec.choices(changelist))[:5]:
            # unfold's form-based filters (e.g. RangeDateFilter) yield
            # choices without query_string - they apply on submit instead.
            query = choice.get("query_string")
            if not query or query in ("?", ""):
                continue  # form filter or the "All" reset link
            filtered = su_client.get(page_url(model, "changelist") + query)
            assert filtered.status_code == 200, (spec, query)


@parametrize_all
def test_sorting_every_column_both_directions(
    su_client: Client, model: type[Model]
) -> None:
    url = page_url(model, "changelist")
    columns = len(su_client.get(url).context["cl"].list_display)
    for index in range(1, columns + 1):
        for order in (index, -index):
            response = su_client.get(url, {"o": str(order)})
            assert response.status_code == 200, f"o={order}"


# --- object pages --------------------------------------------------------


@parametrize_all
def test_add_page_respects_permission(
    su_client: Client, superuser: Any, model: type[Model]
) -> None:
    allowed = admin_for(model).has_add_permission(_request(superuser))
    response = su_client.get(page_url(model, "add"))
    assert response.status_code == (200 if allowed else 403)


@parametrize_all
def test_change_page_and_unchanged_save_roundtrip(
    su_client: Client, superuser: Any, model: type[Model]
) -> None:
    """GET the form and re-POST it unchanged (a _continue-only POST would
    pass on validation failures)."""
    obj = _obj_or_skip(model)
    url = page_url(model, "change", quote(obj.pk))
    response = su_client.get(url)
    assert response.status_code == 200

    if not admin_for(model).has_change_permission(_request(superuser), obj):
        assert su_client.post(url, {}).status_code == 403
        return
    data = form_post_data(response) | {"_continue": "1"}
    saved = su_client.post(url, data)
    errors = (
        saved.context["adminform"].form.errors if saved.status_code == 200 else None
    )
    assert saved.status_code == 302, f"unchanged save rejected: {errors}"


@parametrize_all
def test_delete_page_respects_permission(
    su_client: Client, superuser: Any, model: type[Model]
) -> None:
    obj = _obj_or_skip(model)
    allowed = admin_for(model).has_delete_permission(_request(superuser), obj)
    response = su_client.get(page_url(model, "delete", quote(obj.pk)))
    assert response.status_code == (200 if allowed else 403)


@parametrize_all
def test_history_page(su_client: Client, model: type[Model]) -> None:
    obj = _obj_or_skip(model)
    response = su_client.get(page_url(model, "history", quote(obj.pk)))
    assert response.status_code == 200


# --- export ---------------------------------------------------------------


@parametrize_exportable
def test_export_csv_has_rows(su_client: Client, model: type[Model]) -> None:
    url = page_url(model, "export")
    form_page = su_client.get(url)
    assert form_page.status_code == 200
    data: dict[str, str] = {"format": "0", "resource": "0"}
    # SelectableFieldsExportForm: check every offered column checkbox.
    for name, field in form_page.context["form"].fields.items():
        if getattr(field, "is_selectable_field", False):
            data[name] = "on"
    response = su_client.post(url, data)
    assert response.status_code == 200
    assert "text/csv" in response["Content-Type"]
    lines = response.content.decode().strip().splitlines()
    assert len(lines) >= 2, "export produced headers but no rows"


# --- custom detail actions --------------------------------------------------


def test_detail_actions_resolve(su_client: Client, superuser: Any) -> None:
    """Every actions_detail button a permitted user is offered must resolve
    and respond - custom action URLs escape the standard page tests."""
    targets = [
        (model, model_admin)
        for model in ALL_MODELS
        if getattr(model_admin := admin_for(model), "actions_detail", None)
    ]
    if not targets:
        pytest.skip("no admin declares actions_detail yet")
    for model, model_admin in targets:
        obj = _obj_or_skip(model)
        actions = cast("Any", model_admin).get_actions_detail(
            _request(superuser), obj.pk
        )
        for action in actions:
            url = reverse(f"admin:{action.action_name}", args=[quote(obj.pk)])
            response = su_client.get(url)
            assert response.status_code in (200, 302), (model, action.action_name)


# --- autocomplete -----------------------------------------------------------


def test_autocomplete_endpoints(su_client: Client) -> None:
    targets = [
        (model, field)
        for model in ALL_MODELS
        for field in admin_for(model).autocomplete_fields
    ]
    if not targets:
        pytest.skip("no autocomplete_fields in any admin")
    for model, field in targets:
        opts = model._meta
        response = su_client.get(
            "/admin/autocomplete/",
            {
                "app_label": opts.app_label,
                "model_name": str(opts.model_name),
                "field_name": field,
                "term": "",
            },
        )
        assert response.status_code == 200, (model, field)


# --- cross-cutting invariants ------------------------------------------------


@parametrize_local
def test_data_coverage(model: type[Model]) -> None:
    """An admin tested against an empty table proves nothing."""
    assert model._default_manager.exists(), (
        f"{model._meta.label} has no seeded rows - its factory must create "
        "them (see apps/common/tests/factories_registry.py)."
    )


@parametrize_local
def test_local_admins_use_the_framework(model: type[Model]) -> None:
    assert isinstance(admin_for(model), BaseModelAdmin), (
        f"{model._meta.label}'s admin must subclass apps.common.admin.BaseModelAdmin."
    )


@parametrize_local
def test_inlines_use_the_framework(model: type[Model]) -> None:
    """Inline capability + field-permission discipline only holds if every
    inline subclasses the framework bases (can_* enforced at import)."""
    inlines = getattr(admin_for(model), "inlines", ())
    if not inlines:
        pytest.skip("no inlines declared")
    for inline in inlines:
        assert issubclass(inline, BaseTabularInline | BaseStackedInline), (
            f"{model._meta.label}: {inline.__name__} must subclass "
            "BaseTabularInline/BaseStackedInline (apps.common.admin)."
        )


@parametrize_local
def test_list_editable_cannot_bypass_field_rules(model: type[Model]) -> None:
    """Changelist bulk-editing ignores per-request field rules, so a ruled
    field (or its translation shadow) must never be list_editable."""
    model_admin = admin_for(model)
    if not isinstance(model_admin, BaseModelAdmin):
        pytest.skip("not a framework admin")
    ruled = tuple(
        set(model_admin.field_permissions.readonly_when)
        | set(model_admin.field_permissions.hidden_when)
    )
    expanded = set(
        expand_translation_shadows(
            ruled, {field.name for field in model._meta.get_fields()}
        )
    )
    overlap = expanded & set(model_admin.list_editable or ())
    assert not overlap, (
        f"{model._meta.label}: {sorted(overlap)} in list_editable would bypass "
        "field_permissions on the changelist."
    )


def test_sidebar_permission_consistency(
    su_client: Client,
    staff_client: Client,
    superuser: Any,
    permless_staff: Any,
) -> None:
    """A sidebar item's permission callable must agree with its target page:
    hidden exactly when the user cannot open it (adopted from Gawdat)."""
    unfold = cast("dict[str, Any]", settings.UNFOLD)
    navigation = unfold["SIDEBAR"]["navigation"]
    items = [item for group in navigation for item in group["items"]]
    assert items, "UNFOLD sidebar is empty"
    for item in items:
        link = str(item["link"])
        assert item["permission"](_request(superuser, link)) is True, link
        assert su_client.get(link).status_code == 200, link
        staff_allowed = bool(item["permission"](_request(permless_staff, link)))
        staff_status = staff_client.get(link).status_code
        assert staff_allowed == (staff_status == 200), (
            f"{link}: sidebar permission says {staff_allowed} but the page "
            f"returned {staff_status} - callable and page perms disagree."
        )


def _field_value(obj: Model, field: str) -> Any:
    value = getattr(obj, field)
    if hasattr(value, "values_list"):  # M2M manager
        return set(value.values_list("pk", flat=True))
    return value


def test_hidden_fields_cannot_be_posted(
    su_client: Client,
    superuser: Any,
    priv_staff_client: Client,
    priv_staff: Any,
) -> None:
    """Tamper check (admin v2 review): a value POSTed for a hidden_when
    field must never persist - proven for the superuser AND for a fully
    permissioned non-superuser staff (the privilege-escalation case)."""
    targets = [
        (model, model_admin)
        for model in LOCAL_MODELS
        if isinstance(model_admin := admin_for(model), BaseModelAdmin)
        and model_admin.field_permissions.hidden_when
    ]
    if not targets:
        pytest.skip("no admin declares hidden_when rules yet")
    tampered = 0
    for model, model_admin in targets:
        for client, user in ((su_client, superuser), (priv_staff_client, priv_staff)):
            request = _request(user)
            obj = model_admin.get_queryset(request).first()
            if obj is None or not model_admin.has_change_permission(request, obj):
                continue
            hidden = model_admin.hidden_rule_fields(
                AdminContext(request=request, obj=obj)
            )
            if not hidden:
                continue
            url = page_url(model, "change", quote(obj.pk))
            before = {field: _field_value(obj, field) for field in hidden}
            data = form_post_data(client.get(url))
            for field in hidden:
                data[field] = "TAMPERED"
            assert client.post(url, data | {"_continue": "1"}).status_code == 302
            obj.refresh_from_db()
            for field in hidden:
                assert _field_value(obj, field) == before[field], (model, field)
            tampered += 1
    assert tampered, "hidden_when rules exist but no tamper POST was exercised"
