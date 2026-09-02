from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.meli import (
    CATEGORY_HOUSES_RENT,
    CITY_TIGRE,
    SEARCH_URL,
    USER_AGENT,
    fetch_listings,
    listings_from_search,
)
from src.prefs import load_prefs

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "meli.json"

KEEP_IDS = {
    "MLA900000001",  # USD 2200 en rango de notify
    "MLA900000002",  # USD 3200 watch, no filtrar 1500–2500 acá
    "MLA900000003",  # solo ambientes >= 5
    "MLA900000004",  # ARS → price_usd None
    "MLA900000005",  # Villa Pacheco, gated/pileta por texto
    "MLA900000006",  # Pacheco Golf, tope watch 3500
}

DROP_IDS = {
    "MLA900000007",  # Nordelta
    "MLA900000008",  # Tigre centro
    "MLA900000009",  # 3 dormitorios
    "MLA900000010",  # 4 ambientes ≠ 4 dormitorios
    "MLA900000011",  # sin pileta
    "MLA900000012",  # no gated
    "MLA900000013",  # USD 4000 > watch_max
    "MLA900000014",  # departamento
}


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _listings():
    return listings_from_search(_fixture(), load_prefs())


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
        assert listing.is_gated is True
        assert listing.has_pool is True
        assert listing.url
        assert listing.raw_hash
        assert listing.external_id == listing.external_id.strip()

    again = listings_from_search(_fixture(), load_prefs())
    assert [listing.external_id for listing in again] == [listing.external_id for listing in listings]


def test_keeps_watch_prices_and_does_not_apply_notify_band() -> None:
    by_id = _by_id()

    assert by_id["MLA900000001"].price_usd == 2200
    assert by_id["MLA900000002"].price_usd == 3200
    assert by_id["MLA900000006"].price_usd == 3500
    assert "MLA900000013" not in by_id


def test_drops_nordelta_and_tigre_centro_without_pacheco() -> None:
    by_id = _by_id()
    assert "MLA900000007" not in by_id
    assert "MLA900000008" not in by_id
    assert by_id["MLA900000005"].barrio_name == "Villa Pacheco"
    assert by_id["MLA900000006"].barrio_name == "Pacheco Golf Club"


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


def test_pool_and_gated_need_evidence() -> None:
    ids = {listing.external_id for listing in _listings()}
    assert "MLA900000011" not in ids
    assert "MLA900000012" not in ids
    assert "MLA900000014" not in ids
    text_evidence = _by_id()["MLA900000005"]
    assert text_evidence.is_gated is True
    assert text_evidence.has_pool is True


def test_fetch_listings_uses_public_search_and_fixture() -> None:
    payload = _fixture()
    response = MagicMock()
    response.status_code = 200
    response.raise_for_status.return_value = None
    response.json.return_value = payload
    client = MagicMock()
    client.get.return_value = response

    listings = fetch_listings(load_prefs(), client=client)

    client.get.assert_called_once()
    args, kwargs = client.get.call_args
    assert args[0] == SEARCH_URL
    assert kwargs["params"]["category"] == CATEGORY_HOUSES_RENT
    assert kwargs["params"]["city"] == CITY_TIGRE
    assert kwargs["headers"]["User-Agent"] == USER_AGENT
    assert {listing.external_id for listing in listings} == KEEP_IDS


def test_fetch_listings_403_asks_for_oauth_token(monkeypatch) -> None:
    from src.adapters.errors import AdapterFetchError

    monkeypatch.delenv("MELI_ACCESS_TOKEN", raising=False)
    response = MagicMock()
    response.status_code = 403
    client = MagicMock()
    client.get.return_value = response

    with pytest.raises(AdapterFetchError, match="MELI_ACCESS_TOKEN"):
        fetch_listings(load_prefs(), client=client)


def test_fetch_listings_403_with_token_says_rejected(monkeypatch) -> None:
    from src.adapters.errors import AdapterFetchError

    monkeypatch.setenv("MELI_ACCESS_TOKEN", "APP_USR-123-expired-token-placeholder-xxxx")
    response = MagicMock()
    response.status_code = 403
    client = MagicMock()
    client.get.return_value = response

    with pytest.raises(AdapterFetchError, match="rechazó el access token"):
        fetch_listings(load_prefs(), client=client)


def test_fetch_listings_403_secret_looking_token(monkeypatch) -> None:
    from src.adapters.errors import AdapterFetchError

    monkeypatch.setenv("MELI_ACCESS_TOKEN", "a" * 32)
    response = MagicMock()
    response.status_code = 403
    client = MagicMock()
    client.get.return_value = response

    with pytest.raises(AdapterFetchError, match="APP_USR-"):
        fetch_listings(load_prefs(), client=client)


def _meli_categories_reachable() -> bool:
    try:
        response = httpx.get(
            f"https://api.mercadolibre.com/categories/{CATEGORY_HOUSES_RENT}",
            headers={"User-Agent": USER_AGENT},
            timeout=5.0,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def test_optional_live_search() -> None:
    if not _meli_categories_reachable():
        pytest.skip("sin red")
    try:
        listings = fetch_listings(load_prefs())
    except (httpx.HTTPStatusError, Exception) as exc:
        from src.adapters.errors import AdapterFetchError

        if isinstance(exc, AdapterFetchError):
            pytest.skip("búsqueda ML no pública sin token")
        if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in {401, 403}:
            pytest.skip("búsqueda ML no pública sin token")
        raise

    assert isinstance(listings, list)
    for listing in listings:
        assert listing.source == "meli"
        assert listing.external_id
        assert listing.property_type == "house"
        assert "pacheco" in listing.locality.lower() or (
            listing.barrio_name and "pacheco" in listing.barrio_name.lower()
        )
