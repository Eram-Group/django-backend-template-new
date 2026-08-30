"""The "Load zones" sheet and the overlap action."""

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse

from apps.location.tests.factories import CountryFactory
from apps.users.tests.factories import UserFactory
from apps.zones.models import Zone
from apps.zones.tests.factories import ZoneFactory
from apps.zones.tests.factories import square_multipolygon
from apps.zones.tests.geo import collection
from apps.zones.tests.geo import feature

pytestmark = pytest.mark.django_db

LOAD_URL = reverse("admin:zones_zone_load_zones")
CHANGELIST_URL = reverse("admin:zones_zone_changelist")


@pytest.fixture
def admin(client: Client) -> Client:
    client.force_login(UserFactory.create(staff=True, is_superuser=True))
    # Assertions read the English copy; the site default is Arabic.
    client.defaults["HTTP_ACCEPT_LANGUAGE"] = "en"
    return client


def _upload(document: bytes) -> SimpleUploadedFile:
    return SimpleUploadedFile("zones.geojson", document, "application/geo+json")


def test_changelist_offers_the_load_button(admin: Client) -> None:
    response = admin.get(CHANGELIST_URL)
    assert response.status_code == 200
    assert LOAD_URL in response.content.decode()


def test_sheet_renders_with_active_countries(admin: Client) -> None:
    saudi = CountryFactory.create(code="SA")
    egypt = CountryFactory.create(code="EG", is_active=False)
    response = admin.get(LOAD_URL)
    assert response.status_code == 200
    html = response.content.decode()
    assert f'value="{saudi.pk}"' in html
    assert f'value="{egypt.pk}"' not in html


def test_post_loads_the_file(admin: Client) -> None:
    saudi = CountryFactory.create(code="SA")
    doc = collection(feature(zone_code=1), feature(zone_code=2, name_ar=""))
    response = admin.post(
        LOAD_URL, {"country": saudi.pk, "document": _upload(doc)}, follow=True
    )
    assert response.redirect_chain[-1][0] == CHANGELIST_URL
    assert (
        "Loaded 2 new and updated 0 zones; 1 need a name" in response.content.decode()
    )
    assert Zone.objects.filter(country=saudi).count() == 2


def test_post_reports_a_file_problem_on_the_sheet(admin: Client) -> None:
    saudi = CountryFactory.create(code="SA")
    doc = collection(feature(country_code="EG"))
    response = admin.post(LOAD_URL, {"country": saudi.pk, "document": _upload(doc)})
    assert response.status_code == 200
    assert "belongs to EG, not Saudi Arabia" in response.content.decode()
    assert not Zone.objects.filter(country=saudi).exists()


def test_post_requires_country_and_file(admin: Client) -> None:
    response = admin.post(LOAD_URL, {})
    assert response.status_code == 200
    assert response.context["form"].errors.keys() == {"country", "document"}


def test_load_needs_add_permission(client: Client) -> None:
    viewer = UserFactory.create(staff=True)
    viewer.user_permissions.add(Permission.objects.get(codename="view_zone"))
    client.force_login(viewer)
    assert client.get(LOAD_URL).status_code == 403


def test_change_form_shows_geometry_summary_and_saves(admin: Client) -> None:
    zone = ZoneFactory.create()
    url = reverse("admin:zones_zone_change", args=[zone.pk])
    html = admin.get(url).content.decode()
    assert "Polygons: 1" in html
    assert "Vertices: 5" in html


def test_overlap_action_reports_codes(admin: Client) -> None:
    geometry = square_multipolygon(9_999)
    lone = ZoneFactory.create(code="sa-x-1")
    a = ZoneFactory.create(code="sa-x-2", geometry=geometry)
    b = ZoneFactory.create(code="sa-x-3", geometry=geometry)
    response = admin.post(
        CHANGELIST_URL,
        {"action": "find_overlaps", "_selected_action": [lone.pk, a.pk, b.pk]},
        follow=True,
    )
    html = response.content.decode()
    assert "2 selected zones overlap another zone: sa-x-2, sa-x-3" in html
    response = admin.post(
        CHANGELIST_URL,
        {"action": "find_overlaps", "_selected_action": [lone.pk]},
        follow=True,
    )
    assert "No overlaps among the selected zones." in response.content.decode()
