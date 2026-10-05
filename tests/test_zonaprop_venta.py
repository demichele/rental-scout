from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.zonaprop import (
    BASE_URL,
    ROBOTS_URL,
    FetchNotAllowed,
    fetch_listings,
    listings_from_html,
    search_paths,
    search_url,
)
from src.prefs import load_sale_prefs

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "zonaprop_venta.html"

KEEP_IDS = {"ZP-SALE-001", "ZP-SALE-002", "ZP-SALE-003"}
DROP_IDS = {"ZP-SALE-004", "ZP-SALE-005", "ZP-SALE-006", "ZP-SALE-007"}

SAMPLE_ROBOTS = """
User-agent: *
Disallow: /avisos-api/
Allow: /*pagina-2.html$
Allow: /*pagina-3.html$
Allow: /*pagina-4.html$
Allow: /*pagina-5.html$
Disallow: /*pagina-*.html
"""


@pytest.fixture
def sale_prefs():
    prefs = load_sale_prefs()
    assert prefs is not None
    return prefs


def _html() -> str:
    return FIXTURE_PATH.read_text(encoding="utf-8")


def test_sale_search_paths_are_tigre_and_pacheco(sale_prefs) -> None:
    paths = search_paths(sale_prefs)
    assert paths == [
        "/casas-venta-tigre.html",
        "/casas-venta-general-pacheco.html",
    ]
    assert "/casas-venta-pacheco.html" not in paths
    assert all("alquiler" not in path for path in paths)
    assert "menos-80000" not in search_url(path=paths[0])
    assert search_url(path=paths[0]) == f"{BASE_URL}/casas-venta-tigre.html"
    assert search_url(path=paths[1], page=2) == f"{BASE_URL}/casas-venta-general-pacheco-pagina-2.html"


def test_sale_fixture_keeps_deals_and_drops_the_rest(sale_prefs) -> None:
    listings = listings_from_html(_html(), sale_prefs)
    ids = {listing.external_id for listing in listings}
    assert ids == KEEP_IDS
    assert ids.isdisjoint(DROP_IDS)
    for listing in listings:
        assert listing.source == "zonaprop_venta"
        assert listing.operation == "sale"
        assert listing.property_type == "house"
        assert listing.price_usd is not None
        assert listing.price_usd <= 80000


def test_sale_keeps_small_houses_without_bedroom_minimum(sale_prefs) -> None:
    by_id = {listing.external_id: listing for listing in listings_from_html(_html(), sale_prefs)}
    assert by_id["ZP-SALE-001"].bedrooms == 2
    assert by_id["ZP-SALE-001"].price_usd == 55000
    assert by_id["ZP-SALE-001"].locality == "Tigre"
    assert by_id["ZP-SALE-002"].locality == "General Pacheco"
    assert by_id["ZP-SALE-002"].price_usd == 80000
    assert by_id["ZP-SALE-003"].bedrooms is None
    assert by_id["ZP-SALE-003"].price_usd == 40000


def test_sale_drops_over_cap_depto_and_other_partido(sale_prefs) -> None:
    ids = {listing.external_id for listing in listings_from_html(_html(), sale_prefs)}
    assert "ZP-SALE-004" not in ids
    assert "ZP-SALE-005" not in ids
    assert "ZP-SALE-006" not in ids
    assert "ZP-SALE-007" not in ids


def test_sale_fetch_uses_venta_serps(sale_prefs) -> None:
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

    requested: list[str] = []

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        requested.append(str(url))
        page.url = httpx.URL(str(url))
        return page

    client = MagicMock()
    client.get.side_effect = fake_get

    listings = fetch_listings(sale_prefs, client=client)
    assert {listing.external_id for listing in listings} == KEEP_IDS
    assert any("/casas-venta-tigre.html" in url for url in requested)
    assert any("/casas-venta-general-pacheco.html" in url for url in requested)
    assert all("alquiler" not in url for url in requested)


def test_sale_fetch_skips_off_locality_redirect(sale_prefs) -> None:
    robots = MagicMock()
    robots.status_code = 200
    robots.headers = {}
    robots.text = SAMPLE_ROBOTS
    robots.raise_for_status.return_value = None

    nationwide = MagicMock()
    nationwide.status_code = 200
    nationwide.headers = {"content-type": "text/html"}
    nationwide.text = _html()
    nationwide.url = httpx.URL(f"{BASE_URL}/casas-venta.html")
    nationwide.raise_for_status.return_value = None

    def fake_get(url, **kwargs):
        if str(url) == ROBOTS_URL:
            return robots
        return nationwide

    client = MagicMock()
    client.get.side_effect = fake_get

    with pytest.raises(FetchNotAllowed, match="ninguna SERP usable"):
        fetch_listings(sale_prefs, client=client)
