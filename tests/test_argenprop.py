from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.argenprop import (
    BASE_URL,
    ROBOTS_URL,
    USER_AGENT,
    FetchNotAllowed,
    fetch_listings,
    listings_from_html,
    search_url,
)
from src.prefs import load_prefs

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "argenprop.html"

KEEP_IDS = {
    "AP900000001",  # USD 2200 en rango de notify
    "AP900000002",  # USD 3200 watch, no filtrar 1500–2500 acá
    "AP900000003",  # solo ambientes >= 5
    "AP900000004",  # ARS → price_usd None
    "AP900000005",  # Villa Pacheco, gated/pileta por texto
    "AP900000006",  # Pacheco Golf, tope watch 3500
}

DROP_IDS = {
    "AP900000007",  # Nordelta
    "AP900000008",  # Tigre centro
    "AP900000009",  # 3 dormitorios
    "AP900000010",  # 4 ambientes ≠ 4 dormitorios
    "AP900000011",  # sin pileta
    "AP900000012",  # no gated
    "AP900000013",  # USD 4000 > watch_max
    "AP900000014",  # departamento
}

SAMPLE_ROBOTS = """
User-agent: *
Disallow: /api/
Allow: /*?pagina-1$
Allow: /*?pagina-2$
Allow: /*?pagina-3$
Disallow: /*?pagina-
"""


def _html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def _listings():
    return listings_from_html(_html(), load_prefs())


def _by_id():
    return {listing.external_id: listing for listing in _listings()}


def test_fixture_maps_listings_with_stable_argenprop_ids() -> None:
    listings = _listings()
    ids = {listing.external_id for listing in listings}

    assert ids == KEEP_IDS
    assert ids.isdisjoint(DROP_IDS)
    for listing in listings:
        assert listing.source == "argenprop"
        assert listing.property_type == "house"
        assert listing.locality == "General Pacheco"
        assert listing.is_gated is True
        assert listing.has_pool is True
        assert listing.url
        assert listing.raw_hash
        assert listing.external_id == listing.external_id.strip()

    again = listings_from_html(_html(), load_prefs())
    assert [listing.external_id for listing in again] == [listing.external_id for listing in listings]


def test_keeps_watch_prices_and_does_not_apply_notify_band() -> None:
    by_id = _by_id()

    assert by_id["AP900000001"].price_usd == 2200
    assert by_id["AP900000002"].price_usd == 3200
    assert by_id["AP900000006"].price_usd == 3500
    assert "AP900000013" not in by_id


def test_drops_nordelta_and_tigre_centro_without_pacheco() -> None:
    by_id = _by_id()
    assert "AP900000007" not in by_id
    assert "AP900000008" not in by_id
    assert by_id["AP900000005"].barrio_name == "Villa Pacheco"
    assert by_id["AP900000006"].barrio_name == "Pacheco Golf Club"


def test_bedrooms_not_confused_with_ambientes() -> None:
    by_id = _by_id()

    assert by_id["AP900000001"].bedrooms == 4
    assert by_id["AP900000003"].bedrooms is None
    assert "AP900000009" not in by_id
    assert "AP900000010" not in by_id


def test_ars_without_fx_leaves_price_usd_none() -> None:
    listing = _by_id()["AP900000004"]
    assert listing.currency == "ARS"
    assert listing.price == 2_000_000
    assert listing.price_usd is None


def test_pool_and_gated_need_evidence() -> None:
    ids = {listing.external_id for listing in _listings()}
    assert "AP900000011" not in ids
    assert "AP900000012" not in ids
    assert "AP900000014" not in ids
    text_evidence = _by_id()["AP900000005"]
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
    assert "api/" not in "".join(urls)
    assert "sosiva451.com/api" not in "".join(urls)
    headers = client.get.call_args_list[1].kwargs["headers"]
    assert headers["User-Agent"] == USER_AGENT
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
    blocked.status_code = 202
    blocked.headers = {}
    blocked.text = "<script>window.gokuProps = {}</script>"

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
    assert url == BASE_URL + "/casas/alquiler/general-pacheco"
    assert "api" not in url
    assert search_url(page=2).endswith("?pagina-2")
    assert search_url(page=3).endswith("?pagina-3")


def test_optional_live_search() -> None:
    try:
        listings = fetch_listings(load_prefs())
    except FetchNotAllowed as exc:
        pytest.skip(str(exc))
    except httpx.HTTPError:
        pytest.skip("sin red")

    assert isinstance(listings, list)
    for listing in listings:
        assert listing.source == "argenprop"
        assert listing.external_id
        assert listing.property_type == "house"
        assert "pacheco" in listing.locality.lower() or (
            listing.barrio_name and "pacheco" in listing.barrio_name.lower()
        )
