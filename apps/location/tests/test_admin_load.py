"""The "Load countries" sheet and the "Fetch flags" action."""

from typing import Any

import httpx
import pytest
import respx
from django.contrib.auth.models import Permission
from django.test import Client
from django.urls import reverse

from apps.location.clients.flags import FLAG_URL
from apps.location.models import Country
from apps.location.tests.factories import TINY_PNG
from apps.location.tests.factories import CountryFactory
from apps.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

LOAD_URL = reverse("admin:location_country_load_countries")
CHANGELIST_URL = reverse("admin:location_country_changelist")


@pytest.fixture
def admin(client: Client) -> Client:
    """A superuser browsing in English, so message assertions read the
    source strings (the Arabic catalog is compiled in the suite)."""
    client.force_login(UserFactory.create(staff=True, is_superuser=True))
    client.defaults["HTTP_ACCEPT_LANGUAGE"] = "en"
    return client


def test_changelist_offers_the_load_button(admin: Client) -> None:
    CountryFactory.create(code="SA")
    response = admin.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert LOAD_URL in response.content.decode()


def test_sheet_lists_loadable_and_marks_loaded(admin: Client) -> None:
    CountryFactory.create(code="SA")
    response = admin.get(LOAD_URL)
    assert response.status_code == 200
    html = response.content.decode()
    assert 'value="EG"' in html or 'value="JO"' in html  # something unloaded
    assert "Saudi Arabia" in html  # loaded rows still listed, greyed


@respx.mock
def test_post_loads_the_picked_codes(admin: Client, run_enqueued_tasks: Any) -> None:
    Country.objects.filter(code="EG").delete()
    respx.get(FLAG_URL.format(code="eg")).mock(
        return_value=httpx.Response(200, content=TINY_PNG)
    )

    with run_enqueued_tasks():
        response = admin.post(LOAD_URL, {"codes": ["EG"]}, follow=True)

    assert response.redirect_chain[-1][0] == CHANGELIST_URL
    assert "Loaded 1 countries." in response.content.decode()
    assert Country.objects.filter(code="EG", currency="EGP").exists()


def test_post_rejects_an_already_loaded_code(admin: Client) -> None:
    CountryFactory.create(code="SA")
    before = Country.objects.count()
    response = admin.post(LOAD_URL, {"codes": ["SA"]})
    assert response.status_code == 200
    assert response.context["form"].errors
    assert Country.objects.count() == before


def test_post_requires_a_selection(admin: Client) -> None:
    response = admin.post(LOAD_URL, {})
    assert response.status_code == 200
    assert "Pick at least one country to load." in response.content.decode()


def test_load_needs_add_permission(client: Client) -> None:
    viewer = UserFactory.create(staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_country"))
    client.force_login(viewer)
    assert client.get(LOAD_URL).status_code == 403


@respx.mock
def test_fetch_flags_action_queues_downloads(
    admin: Client, run_enqueued_tasks: Any
) -> None:
    country = CountryFactory.create(code="OM")
    route = respx.get(FLAG_URL.format(code="om")).mock(
        return_value=httpx.Response(200, content=TINY_PNG)
    )

    with run_enqueued_tasks():
        response = admin.post(
            CHANGELIST_URL,
            {"action": "fetch_flags", "_selected_action": [str(country.pk)]},
            follow=True,
        )

    assert response.status_code == 200
    assert route.called
    country.refresh_from_db()
    assert country.flag.name == "location/flags/om.png"
