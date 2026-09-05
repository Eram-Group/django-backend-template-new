"""The load sheet's parser: file shape, country match, geometry rules."""

import json
from pathlib import Path

import pytest
from django.utils import translation

from apps.location.tests.factories import CountryFactory
from apps.zones.exceptions import ZoneFileError
from apps.zones.geojson import zone_features
from apps.zones.tests.geo import BOWTIE
from apps.zones.tests.geo import HOLE
from apps.zones.tests.geo import SQUARE
from apps.zones.tests.geo import collection
from apps.zones.tests.geo import feature

DATA = Path(__file__).parent / "data"


@pytest.fixture(autouse=True)
def _english():  # type: ignore[no-untyped-def]
    """Error messages are asserted in English; the site default is Arabic."""
    with translation.override("en"):
        yield


@pytest.fixture
def saudi():  # type: ignore[no-untyped-def]
    return CountryFactory.build(code="SA")


def test_polygon_becomes_multipolygon_with_names_and_code(saudi) -> None:  # type: ignore[no-untyped-def]
    [zone] = zone_features(document=collection(feature()), country=saudi)
    assert zone.code == "sa-rd-1"
    assert zone.region_code == "RD"
    assert zone.zone_code == "1"
    assert zone.named
    assert (zone.name_en, zone.name_ar) == ("Riyadh north", "شمال الرياض")
    assert zone.geometry.geom_type == "MultiPolygon"
    assert zone.geometry.srid == 4326
    assert zone.geometry.num_geom == 1


def test_multipolygon_and_holes_are_kept(saudi) -> None:  # type: ignore[no-untyped-def]
    geometry = {"type": "MultiPolygon", "coordinates": [[SQUARE, HOLE]]}
    [zone] = zone_features(
        document=collection(feature(geometry=geometry)), country=saudi
    )
    assert zone.geometry.num_geom == 1
    assert zone.geometry[0].num_interior_rings == 1


def test_alpha_3_country_code_and_string_zone_code_match(saudi) -> None:  # type: ignore[no-untyped-def]
    doc = collection(feature(country_code="SAU", zone_code="13", region_code="mak"))
    [zone] = zone_features(document=doc, country=saudi)
    assert zone.code == "sa-mak-13"
    assert zone.region_code == "MAK"


def test_third_dimension_is_dropped(saudi) -> None:  # type: ignore[no-untyped-def]
    ring = [[x, y, 5.0] for x, y in SQUARE]
    geometry = {"type": "Polygon", "coordinates": [ring]}
    [zone] = zone_features(
        document=collection(feature(geometry=geometry)), country=saudi
    )
    assert not zone.geometry.hasz


def test_names_are_normalised(saudi) -> None:  # type: ignore[no-untyped-def]
    doc = collection(
        feature(name_ar="قسم\xa0ثان\xa0مدينة  نصر ", name_en=" Nasr  City")  # noqa: RUF001
    )
    [zone] = zone_features(document=doc, country=saudi)
    assert (zone.name_ar, zone.name_en) == ("قسم ثان مدينة نصر", "Nasr City")


def test_unnamed_feature_takes_the_code_and_is_flagged(saudi) -> None:  # type: ignore[no-untyped-def]
    doc = collection(feature(name_en="", name_ar=None))  # type: ignore[arg-type]
    [zone] = zone_features(document=doc, country=saudi)
    assert not zone.named
    assert zone.name_en == zone.name_ar == "sa-rd-1"


def test_half_named_feature_keeps_the_given_name(saudi) -> None:  # type: ignore[no-untyped-def]
    [zone] = zone_features(document=collection(feature(name_ar="")), country=saudi)
    assert not zone.named
    assert zone.name_en == "Riyadh north"
    assert zone.name_ar == "sa-rd-1"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (b"\xff\xfe", "UTF-8"),
        (b"{not json", "not valid JSON"),
        (json.dumps({"type": "Feature"}).encode(), "FeatureCollection"),
        (json.dumps([1, 2]).encode(), "FeatureCollection"),
        (
            json.dumps({"type": "FeatureCollection", "features": []}).encode(),
            "no features",
        ),
        (collection({"type": "Feature"}), "no properties"),
        (collection(feature(country_code="EG")), "belongs to EG, not Saudi Arabia"),
        (collection(feature(country_code="")), "belongs to -, not"),
        (collection(feature(region_code="")), "missing region_code or zone_code"),
        (collection(feature(zone_code=None)), "missing region_code or zone_code"),
        (collection(feature(), feature()), "both map to code sa-rd-1"),
        (
            collection(feature(geometry={"type": "Point", "coordinates": [1, 2]})),
            "got Point",
        ),
        (
            collection(feature(geometry={"type": "Polygon", "coordinates": [BOWTIE]})),
            "invalid geometry",
        ),
        (
            collection(feature(geometry={"type": "Polygon", "coordinates": "x"})),
            "unreadable geometry",
        ),
        (
            collection(feature(geometry={"type": "Polygon", "coordinates": []})),
            "empty geometry",
        ),
    ],
)
def test_rejections_name_the_problem(saudi, document: bytes, message: str) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ZoneFileError, match=message):
        zone_features(document=document, country=saudi)


def test_feature_index_is_one_based(saudi) -> None:  # type: ignore[no-untyped-def]
    doc = collection(feature(), feature(zone_code=2, region_code=""))
    with pytest.raises(ZoneFileError, match="Feature 2 is missing"):
        zone_features(document=doc, country=saudi)


def test_oversized_document_is_refused(saudi, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr("apps.zones.geojson.MAX_DOCUMENT_BYTES", 10)
    with pytest.raises(ZoneFileError, match="larger than 0 MB"):
        zone_features(document=collection(feature()), country=saudi)


def test_real_source_file_loads() -> None:
    """Three features straight out of the ARE/Abu_dabhi.geojson export."""
    emirates = CountryFactory.build(code="AE")
    zones = zone_features(
        document=(DATA / "abu_dhabi_3.geojson").read_bytes(), country=emirates
    )
    assert [z.code for z in zones] == ["ae-az-26", "ae-az-27", "ae-az-25"]
    assert all(z.named and z.geometry.valid for z in zones)
    assert zones[0].name_ar == "أبوظبى"
