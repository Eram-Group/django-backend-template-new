"""GET /api/v1/location/countries - public, locale-aware, unpaginated."""

import pytest
from django.test import Client

from apps.location.tests.factories import CountryFactory

pytestmark = pytest.mark.django_db

URL = "/api/v1/location/countries"


def test_list_is_public_and_language_aware(client: Client) -> None:
    CountryFactory.create(code="SA")

    arabic = client.get(URL, headers={"Accept-Language": "ar"})
    english = client.get(URL, headers={"Accept-Language": "en"})

    assert arabic.status_code == english.status_code == 200
    by_code_ar = {row["code"]: row for row in arabic.json()}
    by_code_en = {row["code"]: row for row in english.json()}
    assert by_code_ar["SA"]["name"] == "المملكة العربية السعودية"
    assert by_code_en["SA"]["name"] == "Saudi Arabia"
    assert set(by_code_en["SA"]) == {
        "id",
        "code",
        "name",
        "dial_code",
        "phone_example",
        "max_phone_length",
        "currency",
        "flag_url",
    }
    assert by_code_en["SA"]["dial_code"] == "+966"
    assert by_code_en["SA"]["flag_url"] is None


def test_inactive_countries_are_excluded(client: Client) -> None:
    CountryFactory.create(code="EG", is_active=False)
    codes = {row["code"] for row in client.get(URL).json()}
    assert "EG" not in codes


def test_flag_url_is_served_when_a_flag_exists(client: Client) -> None:
    CountryFactory.create(code="AE", with_flag=True)
    row = next(r for r in client.get(URL).json() if r["code"] == "AE")
    assert row["flag_url"].endswith("/ae.png")


def test_list_is_ordered_by_name_in_the_active_language(client: Client) -> None:
    CountryFactory.create(code="SA")
    CountryFactory.create(code="EG")
    names = [
        row["name"] for row in client.get(URL, headers={"Accept-Language": "en"}).json()
    ]
    assert names == sorted(names)
