from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.meli import (
    BASE_URL,
    ROBOTS_URL,
    USER_AGENT,
    FetchNotAllowed,
    fetch_listings,
    listings_from_html,
    search_url,
)
from src.prefs import load_prefs

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "meli.html"

KEEP_IDS = {
    "MLA900000001",  # USD 2200 en rango de notify
    "MLA900000003",  # solo ambientes >= 5
    "MLA900000004",  # ARS → price_usd None
    "MLA900000005",  # Villa Pacheco
    "MLA900000011",  # sin pileta
    "MLA900000012",  # no gated
}

DROP_IDS = {
    "MLA900000002",  # USD 3200 > watch_max 2500
    "MLA900000006",  # USD 3500 > watch_max 2500
    "MLA900000007",  # Nordelta
    "MLA900000008",  # Tigre centro
    "MLA900000009",  # 3 dormitorios y 4 ambientes
    "MLA900000010",  # 4 ambientes, sin dormitorios
    "MLA900000013",  # USD 4000 > watch_max
    "MLA900000014",  # departamento
}

SAMPLE_ROBOTS = """
User-agent: *
Disallow: /*_Desde_
Disallow: /api/
"""


def _html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _listings():
    return listings_from_html(_html(), load_prefs())


def _by_id():
    return {listing.external_id: listing for listing in _listings()}


def test_fixture_maps_listings_with_stable_meli_ids() -> None:
    listings = _listings()
    ids = {listing.external_id for listing in listings}

    assert ids == KEEP_IDS
    assert ids.isdisjoint(DROP_IDS)
    for listing in listings:
        assert listing.source == "meli"
        assert listing.external_id.startswith("MLA")
        assert listing.property_type == "house"
        assert listing.locality == "General Pacheco"
        assert listing.url
        assert listing.url.startswith("https://casa.mercadolibre.com.ar/")
        assert listing.raw_hash
        assert listing.external_id == listing.external_id.strip()

    again = listings_from_html(_html(), load_prefs())
    assert [listing.external_id for listing in again] == [listing.external_id for listing in listings]


def test_keeps_watch_prices_and_does_not_apply_notify_band() -> None:
    by_id = _by_id()

    assert by_id["MLA900000001"].price_usd == 2200
    assert "MLA900000002" not in by_id
    assert "MLA900000006" not in by_id
    assert "MLA900000013" not in by_id


def test_drops_nordelta_and_tigre_centro_without_pacheco() -> None:
    by_id = _by_id()
    assert "MLA900000007" not in by_id
    assert "MLA900000008" not in by_id
    assert by_id["MLA900000005"].barrio_name == "Villa Pacheco"


def test_bedrooms_not_confused_with_ambientes() -> None:
    by_id = _by_id()

    assert by_id["MLA900000001"].bedrooms == 4
    assert by_id["MLA900000003"].bedrooms is None
    assert "MLA900000009" not in by_id
    assert "MLA900000010" not in by_id


def test_ars_without_fx_leaves_price_usd_none() -> None:
    listing = _by_id()["MLA900000004"]
    assert listing.currency == "ARS"
    assert listing.price == 2_000_000
    assert listing.price_usd is None


def test_pool_and_gated_are_not_required() -> None:
    ids = {listing.external_id for listing in _listings()}
    assert "MLA900000011" in ids
    assert "MLA900000012" in ids
    assert "MLA900000014" not in ids


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
    assert "api.mercadolibre.com" not in "".join(urls)
    assert "_Desde_" not in "".join(urls)
    headers = client.get.call_args_list[1].kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT
    assert "Authorization" not in headers
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


def test_search_url_is_public_html_serp() -> None:
    url = search_url()
    assert url == BASE_URL + "/casas/alquiler/bsas-gba-norte/tigre/general-pacheco/"
    assert "api.mercadolibre.com" not in url
    assert search_url(page=2).endswith("_Desde_49")


def test_pagination_desde_is_blocked_by_robots() -> None:
    from src.adapters.meli import _allowed_by_robots

    assert _allowed_by_robots(SAMPLE_ROBOTS, search_url()) is True
    assert _allowed_by_robots(SAMPLE_ROBOTS, search_url(page=2)) is False


def test_optional_live_search() -> None:
    try:
        listings = fetch_listings(load_prefs())
    except FetchNotAllowed as exc:
        pytest.skip(str(exc))
    except httpx.HTTPError:
        pytest.skip("sin red")

    assert isinstance(listings, list)
    for listing in listings:
        assert listing.source == "meli"
        assert listing.external_id
        assert listing.property_type == "house"
        assert "pacheco" in listing.locality.lower() or (
            listing.barrio_name and "pacheco" in listing.barrio_name.lower()
        )
