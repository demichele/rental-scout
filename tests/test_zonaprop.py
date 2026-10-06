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
    search_paths,
    search_url,
)
from src.prefs import load_prefs

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zonaprop.html"

KEEP_IDS = {
    "ZP900000001",  # La Comarca USD 2200
    "ZP900000002",  # Los Alisos USD 2400
    "ZP900000003",  # Talar del Lago, solo ambientes >= 5
    "ZP900000004",  # Santa Bárbara ARS → price_usd None
    "ZP900000006",  # Barrancas de Santa Maria, tope watch 2500
    "ZP900000007",  # Nordelta
}

DROP_IDS = {
    "ZP900000005",  # Villa Pacheco (fuera de la lista)
    "ZP900000008",  # Tigre centro
    "ZP900000009",  # 3 dormitorios y 4 ambientes
    "ZP900000010",  # 4 ambientes, sin dormitorios
    "ZP900000011",  # General Pacheco (fuera de la lista)
    "ZP900000012",  # General Pacheco (fuera de la lista)
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
        assert listing.url
        assert listing.url.startswith("https://www.zonaprop.com.ar/")
        assert listing.raw_hash
        assert listing.external_id == listing.external_id.strip()

    again = listings_from_html(_html(), load_prefs())
    assert [listing.external_id for listing in again] == [listing.external_id for listing in listings]


def test_keeps_watch_prices_and_does_not_apply_notify_band() -> None:
    by_id = _by_id()

    assert by_id["ZP900000001"].price_usd == 2200
    assert by_id["ZP900000002"].price_usd == 2400
    assert by_id["ZP900000006"].price_usd == 2500
    assert "ZP900000013" not in by_id


def test_keeps_listed_barrios_and_drops_others() -> None:
    by_id = _by_id()
    assert by_id["ZP900000001"].barrio_name == "La Comarca"
    assert by_id["ZP900000002"].barrio_name == "Los Alisos"
    assert by_id["ZP900000003"].barrio_name == "Talar del Lago"
    assert by_id["ZP900000004"].barrio_name == "Santa Bárbara"
    assert by_id["ZP900000006"].barrio_name == "Barrancas de Santa Maria"
    assert by_id["ZP900000007"].barrio_name == "Nordelta"
    assert "ZP900000005" not in by_id
    assert "ZP900000008" not in by_id


def test_four_ambientes_are_not_five() -> None:
    by_id = _by_id()

    assert by_id["ZP900000001"].bedrooms == 4
    assert by_id["ZP900000001"].ambientes == 5
    assert by_id["ZP900000003"].bedrooms is None
    assert by_id["ZP900000003"].ambientes == 5
    assert "ZP900000009" not in by_id
    assert "ZP900000010" not in by_id


def test_ars_without_fx_leaves_price_usd_none() -> None:
    listing = _by_id()["ZP900000004"]
    assert listing.currency == "ARS"
    assert listing.price == 2_000_000
    assert listing.price_usd is None


def test_pool_and_gated_are_not_required() -> None:
    ids = {listing.external_id for listing in _listings()}
    assert "ZP900000014" not in ids
    nordelta = _by_id()["ZP900000007"]
    assert nordelta.barrio_name == "Nordelta"


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
    expected_paths = search_paths(prefs)
    assert urls[1:] == [search_url(path=path) for path in expected_paths]
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

    # robots + una SERP por barrio (todas WAF en pág. 1)
    assert client.get.call_count == 1 + len(search_paths(load_prefs()))


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
    prefs = load_prefs()
    paths = search_paths(prefs)
    assert paths[0] == "/casas-alquiler-talar-del-lago-i.html"
    assert paths[1] == "/casas-alquiler-talar-del-lago-ii.html"
    assert "/casas-alquiler-talar-del-lago-1.html" not in paths
    assert "/casas-alquiler-talar-del-lago-2.html" not in paths
    assert "/casas-alquiler-nordelta.html" in paths
    assert "/casas-alquiler-los-alisos.html" in paths
    assert "/casas-alquiler-la-comarca.html" in paths
    assert "/casas-alquiler-santa-barbara.html" in paths
    assert "/casas-alquiler-barrancas-de-santa-maria.html" in paths
    assert "/casas-alquiler-barrancas-de-san-jose.html" in paths
    url = search_url(watch_max_usd=2500, path=paths[0])
    assert url == f"{BASE_URL}/casas-alquiler-talar-del-lago-i.html"
    assert "avisos-api" not in url
    assert "menos-de-" not in url


def test_adelina_search_url_is_villa_adelina_serp() -> None:
    from src.prefs import load_adelina_prefs

    prefs = load_adelina_prefs()
    assert prefs is not None
    paths = search_paths(prefs)
    assert paths == ["/casas-alquiler-villa-adelina.html"]
    assert search_url(path=paths[0]) == f"{BASE_URL}/casas-alquiler-villa-adelina.html"


def test_fetch_skips_serp_redirected_off_barrio() -> None:
    prefs = load_prefs()
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = SAMPLE_ROBOTS
    robots.raise_for_status.return_value = None

    nationwide = MagicMock()
    nationwide.status_code = 200
    nationwide.headers = {"content-type": "text/html"}
    nationwide.text = _html()
    nationwide.url = httpx.URL(f"{BASE_URL}/casas-alquiler.html")
    nationwide.raise_for_status.return_value = None

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        return nationwide

    client = MagicMock()
    client.get.side_effect = fake_get

    with pytest.raises(FetchNotAllowed, match="ninguna SERP usable"):
        fetch_listings(prefs, client=client)


def test_fetch_keeps_on_barrio_serps_when_other_redirect() -> None:
    prefs = load_prefs()
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = SAMPLE_ROBOTS
    robots.raise_for_status.return_value = None

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        page = MagicMock()
        page.status_code = 200
        page.headers = {"content-type": "text/html"}
        page.text = _html()
        page.raise_for_status.return_value = None
        if "talar-del-lago" in str(url):
            page.url = httpx.URL(f"{BASE_URL}/casas-alquiler.html")
        else:
            page.url = httpx.URL(str(url))
        return page

    client = MagicMock()
    client.get.side_effect = fake_get

    listings = fetch_listings(prefs, client=client)
    assert {listing.external_id for listing in listings} == KEEP_IDS


def test_live_schema_casas_plural_and_description_normalized() -> None:
    """La SERP real usa realEstateType=Casas y evidencia en descriptionNormalized."""
    payload = {
        "listStore": {
            "listPostings": [
                {
                    "postingId": "ZP-LIVE-1",
                    "title": "Alquiler en La Comarca",
                    "description": "",
                    "descriptionNormalized": (
                        "Casa en barrio cerrado La Comarca, General Pacheco, con pileta."
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
                            "name": "La Comarca",
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
    assert listing.barrio_name == "La Comarca"
    assert listing.is_gated is True
    assert listing.has_pool is True
    assert listing.bedrooms == 4
    assert listing.currency == "USD"
    assert listing.price_usd == 2200


def test_maps_barrancas_de_san_jose() -> None:
    payload = {
        "listStore": {
            "listPostings": [
                {
                    "postingId": "ZP-SAN-JOSE",
                    "title": "Casa en Barrancas de San José",
                    "descriptionNormalized": "Barrio cerrado Barrancas de San Jose, Tigre, pileta.",
                    "url": "/propiedades/clasificado/alclapin-ZP-SAN-JOSE.html",
                    "realEstateType": {"name": "Casas"},
                    "priceOperationTypes": [{"prices": [{"currency": "USD", "amount": 2300}]}],
                    "mainFeatures": {"CFT3": {"label": "Dormitorios", "value": "4"}},
                    "postingLocation": {
                        "address": {"name": ""},
                        "location": {
                            "name": "Barrancas de San José",
                            "parent": {"name": "Tigre"},
                        },
                    },
                }
            ]
        }
    }
    listings = listings_from_search(payload, load_prefs())
    assert len(listings) == 1
    assert listings[0].barrio_name == "Barrancas de San José"
    assert listings[0].price_usd == 2300


def test_optional_live_search() -> None:
    try:
        listings = fetch_listings(load_prefs())
    except FetchNotAllowed as exc:
        pytest.skip(str(exc))
    except httpx.HTTPError:
        pytest.skip("sin red")

    assert isinstance(listings, list)
    prefs = load_prefs()
    for listing in listings:
        assert listing.source == "zonaprop"
        assert listing.external_id
        assert listing.property_type == "house"
        if listing.price_usd is not None:
            assert listing.price_usd <= prefs.watch_max_price_usd
