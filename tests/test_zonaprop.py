from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.zonaprop import (
    BASE_URL,
    ROBOTS_URL,
    USER_AGENT,
    FetchNotAllowed,
    fetch_listings,
    listings_from_html,
    listings_from_search,
    search_url,
)
from src.prefs import load_prefs

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zonaprop.html"

KEEP_IDS = {
    "ZP900000001",  # USD 2200 en rango de notify
    "ZP900000002",  # USD 3200 watch, no filtrar 1500–2500 acá
    "ZP900000003",  # solo ambientes >= 5
    "ZP900000004",  # ARS → price_usd None
    "ZP900000005",  # Villa Pacheco, gated/pileta por texto
    "ZP900000006",  # Pacheco Golf, tope watch 3500
}

DROP_IDS = {
    "ZP900000007",  # Nordelta
    "ZP900000008",  # Tigre centro
    "ZP900000009",  # 3 dormitorios
    "ZP900000010",  # 4 ambientes ≠ 4 dormitorios
    "ZP900000011",  # sin pileta
    "ZP900000012",  # no gated
    "ZP900000013",  # USD 4000 > watch_max
    "ZP900000014",  # departamento
}

SAMPLE_ROBOTS = """
User-agent: *
Disallow: /avisos-api/
Disallow: /users-api/
Allow: /*pagina-2.html$
Allow: /*pagina-3.html$
Allow: /*pagina-4.html$
Allow: /*pagina-5.html$
Disallow: /*pagina-*.html
"""


def _html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _listings():
    return listings_from_html(_html(), load_prefs())


def _by_id():
    return {listing.external_id: listing for listing in _listings()}


def test_fixture_maps_listings_with_stable_zonaprop_ids() -> None:
    listings = _listings()
    ids = {listing.external_id for listing in listings}

    assert ids == KEEP_IDS
    assert ids.isdisjoint(DROP_IDS)
    for listing in listings:
        assert listing.source == "zonaprop"
        assert listing.property_type == "house"
        assert listing.locality == "General Pacheco"
        assert listing.is_gated is True
        assert listing.has_pool is True
        assert listing.url
        assert listing.url.startswith("https://www.zonaprop.com.ar/")
        assert listing.raw_hash
        assert listing.external_id == listing.external_id.strip()

    again = listings_from_html(_html(), load_prefs())
    assert [listing.external_id for listing in again] == [listing.external_id for listing in listings]


def test_keeps_watch_prices_and_does_not_apply_notify_band() -> None:
    by_id = _by_id()

    assert by_id["ZP900000001"].price_usd == 2200
    assert by_id["ZP900000002"].price_usd == 3200
    assert by_id["ZP900000006"].price_usd == 3500
    assert "ZP900000013" not in by_id


def test_drops_nordelta_and_tigre_centro_without_pacheco() -> None:
    by_id = _by_id()
    assert "ZP900000007" not in by_id
    assert "ZP900000008" not in by_id
    assert by_id["ZP900000005"].barrio_name == "Villa Pacheco"
    assert by_id["ZP900000006"].barrio_name == "Pacheco Golf Club"


def test_bedrooms_not_confused_with_ambientes() -> None:
    by_id = _by_id()

    assert by_id["ZP900000001"].bedrooms == 4
    assert by_id["ZP900000003"].bedrooms is None
    assert "ZP900000009" not in by_id
    assert "ZP900000010" not in by_id


def test_ars_without_fx_leaves_price_usd_none() -> None:
    listing = _by_id()["ZP900000004"]
    assert listing.currency == "ARS"
    assert listing.price == 2_000_000
    assert listing.price_usd is None


def test_pool_and_gated_need_evidence() -> None:
    ids = {listing.external_id for listing in _listings()}
    assert "ZP900000011" not in ids
    assert "ZP900000012" not in ids
    assert "ZP900000014" not in ids
    text_evidence = _by_id()["ZP900000005"]
    assert text_evidence.is_gated is True
    assert text_evidence.has_pool is True


def test_fetch_listings_checks_robots_and_uses_html_serp() -> None:
    prefs = load_prefs()
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = SAMPLE_ROBOTS
    robots.raise_for_status.return_value = None

    page = MagicMock()
    page.status_code = 200
    page.headers = {"content-type": "text/html"}
    page.text = _html()
    page.raise_for_status.return_value = None

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        return page

    client = MagicMock()
    client.get.side_effect = fake_get

    listings = fetch_listings(prefs, client=client)

    urls = [str(call.args[0]) for call in client.get.call_args_list]
    assert urls[0] == ROBOTS_URL
    assert urls[1] == search_url()
    assert "menos-de-" not in urls[1]
    assert "avisos-api" not in "".join(urls)
    assert "bsre.zonaprop" not in "".join(urls)
    headers = client.get.call_args_list[1].kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT
    assert client.get.call_args_list[1].kwargs["follow_redirects"] is True
    assert {listing.external_id for listing in listings} == KEEP_IDS


def test_fetch_raises_when_robots_disallow() -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = "User-agent: *\nDisallow: /\n"
    robots.raise_for_status.return_value = None

    client = MagicMock()
    client.get.return_value = robots

    with pytest.raises(FetchNotAllowed):
        fetch_listings(load_prefs(), client=client)

    assert client.get.call_count == 1
    assert str(client.get.call_args.args[0]) == ROBOTS_URL


def test_fetch_raises_on_waf_without_retry() -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = SAMPLE_ROBOTS
    robots.raise_for_status.return_value = None

    blocked = MagicMock()
    blocked.status_code = 403
    blocked.headers = {"cf-mitigated": "challenge"}
    blocked.text = "Just a moment..."

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        return blocked

    client = MagicMock()
    client.get.side_effect = fake_get

    with pytest.raises(FetchNotAllowed):
        fetch_listings(load_prefs(), client=client)

    assert client.get.call_count == 2


def test_fetch_keeps_earlier_pages_if_later_waf() -> None:
    prefs = load_prefs()
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = SAMPLE_ROBOTS
    robots.raise_for_status.return_value = None

    page = MagicMock()
    page.status_code = 200
    page.headers = {"content-type": "text/html"}
    page.text = _html().replace(
        '"pagesUrl": {}',
        '"pagesUrl": {"nextPage": "/casas-alquiler-general-pacheco-pagina-2.html"}',
    )
    page.raise_for_status.return_value = None

    blocked = MagicMock()
    blocked.status_code = 403
    blocked.headers = {"cf-mitigated": "challenge"}
    blocked.text = "Just a moment..."

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        if "pagina-2" in str(url):
            return blocked
        return page

    client = MagicMock()
    client.get.side_effect = fake_get

    listings = fetch_listings(prefs, client=client)
    assert {listing.external_id for listing in listings} == KEEP_IDS


def test_search_url_is_public_html_serp() -> None:
    url = search_url(watch_max_usd=3500)
    assert url == f"{BASE_URL}/casas-alquiler-general-pacheco.html"
    assert "avisos-api" not in url
    assert "menos-de-" not in url


def test_live_schema_casas_plural_and_description_normalized() -> None:
    """La SERP real usa realEstateType=Casas y evidencia en descriptionNormalized."""
    payload = {
        "listStore": {
            "listPostings": [
                {
                    "postingId": "ZP-LIVE-1",
                    "title": "Alquiler en El Encuentro",
                    "description": "",
                    "descriptionNormalized": (
                        "Casa en barrio cerrado El Encuentro, General Pacheco, con pileta."
                    ),
                    "generatedTitle": "Casa · 270m² · 5 Ambientes",
                    "url": "/propiedades/clasificado/alclapin-ZP-LIVE-1.html",
                    "realEstateType": {"name": "Casas", "realEstateTypeId": "1"},
                    "house": {"type": "House", "name": "Casa · 270m² · 5 Ambientes"},
                    "priceOperationTypes": [
                        {
                            "prices": [
                                {
                                    "currencyId": "2",
                                    "currency": "USD",
                                    "amount": 2200,
                                }
                            ]
                        }
                    ],
                    "mainFeatures": {
                        "CFT2": {"label": "Ambientes", "value": "5"},
                        "CFT3": {"label": "Dormitorios", "value": "4"},
                    },
                    "generalFeatures": {},
                    "highlightedFeatures": [],
                    "postingLocation": {
                        "address": {"name": ""},
                        "location": {
                            "name": "El Encuentro",
                            "parent": {"name": "General Pacheco"},
                        },
                    },
                }
            ]
        }
    }
    listings = listings_from_search(payload, load_prefs())
    assert len(listings) == 1
    listing = listings[0]
    assert listing.source == "zonaprop"
    assert listing.external_id == "ZP-LIVE-1"
    assert listing.is_gated is True
    assert listing.has_pool is True
    assert listing.bedrooms == 4
    assert listing.currency == "USD"
    assert listing.price_usd == 2200


def test_optional_live_search() -> None:
    try:
        listings = fetch_listings(load_prefs())
    except FetchNotAllowed as exc:
        pytest.skip(str(exc))
    except httpx.HTTPError:
        pytest.skip("sin red")

    assert isinstance(listings, list)
    for listing in listings:
        assert listing.source == "zonaprop"
        assert listing.external_id
        assert listing.property_type == "house"
        assert "pacheco" in listing.locality.lower() or (
            listing.barrio_name and "pacheco" in listing.barrio_name.lower()
        )
