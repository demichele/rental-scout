from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import Any

import httpx

from src.adapters.errors import AdapterFetchError
from src.models import Listing
from src.prefs import Prefs, load_prefs

SITE_ID = "MLA"
SEARCH_URL = f"https://api.mercadolibre.com/sites/{SITE_ID}/search"
CATEGORY_HOUSES_RENT = "MLA1467"
CITY_TIGRE = "TUxBQ1RJRzk0ZjQw"
USER_AGENT = "rental-scout personal"
PAGE_SIZE = 50
PAGE_DELAY_SEC = 0.5
REQUEST_TIMEOUT = 20.0
MAX_OFFSET = 1000

_BOOL_YES = {"sí", "si", "yes", "true", "242085"}
_HOUSE_TYPES = {"casa", "house", "chalet", "dúplex", "duplex", "tríplex", "triplex", "cabaña", "cabana"}
_NOT_HOUSE = {"departamento", "depto", "apartamento", "apartment", "ph", "local", "oficina", "terreno"}
_GATED_RE = re.compile(
    r"barrio\s+(?:privado|cerrado)|country\s+club|\bcountry\b",
    re.IGNORECASE,
)
_POOL_RE = re.compile(r"\b(?:pileta|piscina)\b", re.IGNORECASE)
_PACHECO_RE = re.compile(r"pacheco", re.IGNORECASE)

log = logging.getLogger(__name__)


def fetch_listings(
    prefs: Prefs | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[Listing]:
    """Trae casas en alquiler de ML Inmuebles y las mapea a Listing."""
    prefs = prefs or load_prefs()
    payload = _fetch_search(client=client)
    return listings_from_search(payload, prefs)


def listings_from_search(payload: dict[str, Any], prefs: Prefs | None = None) -> list[Listing]:
    """Mapea un JSON de /sites/MLA/search a Listing[] filtrados (sin red)."""
    prefs = prefs or load_prefs()
    listings: list[Listing] = []
    seen: set[str] = set()
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        listing = _map_item(item, prefs)
        if listing is None or listing.external_id in seen:
            continue
        seen.add(listing.external_id)
        listings.append(listing)
    raw = len(payload.get("results") or [])
    log.info("meli mapped %s listings from %s results", len(listings), raw)
    return listings


def _fetch_search(*, client: httpx.Client | None) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        merged: dict[str, Any] = {"site_id": SITE_ID, "results": []}
        offset = 0
        while offset <= MAX_OFFSET:
            response = http.get(
                SEARCH_URL,
                params=_search_params(offset),
                headers=_request_headers(),
                timeout=REQUEST_TIMEOUT,
            )
            if response.status_code in {401, 403}:
                raise AdapterFetchError(_search_auth_error(response.status_code))
            response.raise_for_status()
            page = response.json()
            if offset == 0:
                merged = {**page, "results": []}
            results = page.get("results") or []
            merged["results"].extend(results)
            paging = page.get("paging") or {}
            total = int(paging.get("total") or 0)
            offset += len(results)
            if not results or offset >= total or len(results) < PAGE_SIZE:
                break
            time.sleep(PAGE_DELAY_SEC)
        return merged
    finally:
        if owned:
            http.close()


def _search_params(offset: int) -> dict[str, str | int]:
    return {
        "category": CATEGORY_HOUSES_RENT,
        "city": CITY_TIGRE,
        "limit": PAGE_SIZE,
        "offset": offset,
    }


def _meli_access_token() -> str:
    return os.environ.get("MELI_ACCESS_TOKEN", "").strip()


def _looks_like_user_token(token: str) -> bool:
    """El access_token de usuario es APP_USR-... / TG-...; el secret suele ser ~32 chars."""
    upper = token.upper()
    return upper.startswith(("APP_USR-", "TG-")) or len(token) >= 48


def _search_auth_error(status_code: int) -> str:
    token = _meli_access_token()
    if not token:
        return (
            "Mercado Libre ya no deja buscar sin OAuth "
            f"(HTTP {status_code}). Creá una app en "
            "https://developers.mercadolibre.com.ar , autorizala con "
            "tu usuario, y poné MELI_ACCESS_TOKEN en .env "
            "(Authorization Bearer)."
        )
    if not _looks_like_user_token(token):
        return (
            f"Mercado Libre rechazó MELI_ACCESS_TOKEN (HTTP {status_code}). "
            "Tiene que ser el access_token del usuario (empieza con APP_USR-), "
            "no el Client Secret ni el App ID. Autorizá la app en "
            "https://developers.mercadolibre.com.ar y pegá el token largo."
        )
    return (
        f"Mercado Libre rechazó el access token (HTTP {status_code}). "
        "Renovarlo en https://developers.mercadolibre.com.ar "
        "(autorizar de nuevo y actualizar MELI_ACCESS_TOKEN)."
    )


def _request_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = _meli_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _map_item(item: dict[str, Any], prefs: Prefs) -> Listing | None:
    external_id = str(item.get("id") or "").strip()
    if not external_id:
        return None
    if not _is_house(item, prefs):
        return None
    if not _is_pacheco(item):
        return None

    bedrooms = _attr_int(item, "BEDROOMS")
    rooms = _attr_int(item, "ROOMS")
    if not _enough_bedrooms(bedrooms, rooms, prefs.min_bedrooms):
        return None

    is_gated = _is_gated(item)
    if prefs.gated_only and not is_gated:
        return None

    has_pool = _has_pool(item)
    if prefs.require_pool and not has_pool:
        return None

    currency = str(item.get("currency_id") or "").upper()
    price = float(item.get("price") or 0)
    price_usd = price if currency == "USD" else None
    if price_usd is not None and price_usd > prefs.watch_max_price_usd:
        return None

    neighborhood = _nested_name(item, "location", "neighborhood")
    return Listing(
        source="meli",
        external_id=external_id,
        url=_url(item, external_id),
        title=str(item.get("title") or "").strip() or external_id,
        locality="General Pacheco",
        barrio_name=neighborhood or None,
        is_gated=is_gated,
        bedrooms=bedrooms,
        bathrooms=_attr_float(item, "FULL_BATHROOMS") or _attr_float(item, "BATHROOMS"),
        m2=_attr_float(item, "COVERED_AREA") or _attr_float(item, "TOTAL_AREA"),
        has_pool=has_pool,
        currency=currency or "USD",
        price=price,
        price_usd=price_usd,
        expenses=_attr_float(item, "MAINTENANCE_FEE"),
        property_type="house",
        published_at=_parse_dt(item.get("date_created") or item.get("start_time")),
        photos=_photos(item),
        lat=_coord(item, "latitude"),
        lng=_coord(item, "longitude"),
        raw_hash=_raw_hash(item),
    )


def _is_house(item: dict[str, Any], prefs: Prefs) -> bool:
    wanted = (prefs.property_type or "house").lower()
    if wanted not in {"house", "casa"}:
        return False
    category = str(item.get("category_id") or "")
    prop = (_attr_name(item, "PROPERTY_TYPE") or "").lower()
    subtype = (_attr_name(item, "HOUSE_PROPERTY_SUBTYPE") or "").lower()
    if prop in _NOT_HOUSE or subtype in _NOT_HOUSE:
        return False
    if prop in _HOUSE_TYPES or subtype in _HOUSE_TYPES:
        return True
    if category == CATEGORY_HOUSES_RENT or category.startswith("MLA1466"):
        return True
    title = str(item.get("title") or "").lower()
    return "casa" in title and "departamento" not in title


def _location_text(item: dict[str, Any]) -> str:
    location = item.get("location") or {}
    address = item.get("address") or {}
    parts = [
        item.get("title"),
        (location.get("neighborhood") or {}).get("name") if isinstance(location, dict) else None,
        (location.get("city") or {}).get("name") if isinstance(location, dict) else None,
        location.get("address_line") if isinstance(location, dict) else None,
        address.get("city_name") if isinstance(address, dict) else None,
        address.get("state_name") if isinstance(address, dict) else None,
        address.get("neighborhood") if isinstance(address, dict) else None,
    ]
    return " ".join(str(p) for p in parts if p)


def _is_pacheco(item: dict[str, Any]) -> bool:
    return bool(_PACHECO_RE.search(_location_text(item)))


def _enough_bedrooms(bedrooms: int | None, rooms: int | None, min_bedrooms: int) -> bool:
    if bedrooms is not None:
        return bedrooms >= min_bedrooms
    if rooms is not None:
        return rooms >= min_bedrooms + 1
    return False


def _is_gated(item: dict[str, Any]) -> bool:
    if _attr_bool(item, "IN_GATED_COMMUNITY"):
        return True
    blob = _location_text(item)
    for attr in item.get("attributes") or []:
        blob += " " + str(attr.get("value_name") or "")
    return bool(_GATED_RE.search(blob))


def _has_pool(item: dict[str, Any]) -> bool:
    if _attr_bool(item, "HAS_SWIMMING_POOL"):
        return True
    blob = _location_text(item)
    for attr in item.get("attributes") or []:
        blob += " " + str(attr.get("value_name") or "")
    return bool(_POOL_RE.search(blob))


def _find_attr(item: dict[str, Any], attr_id: str) -> dict[str, Any] | None:
    for attr in item.get("attributes") or []:
        if isinstance(attr, dict) and str(attr.get("id") or "") == attr_id:
            return attr
    return None


def _attr_name(item: dict[str, Any], attr_id: str) -> str | None:
    attr = _find_attr(item, attr_id)
    if not attr:
        return None
    name = attr.get("value_name")
    return str(name).strip() if name is not None and str(name).strip() else None


def _attr_bool(item: dict[str, Any], attr_id: str) -> bool:
    attr = _find_attr(item, attr_id)
    if not attr:
        return False
    value_id = str(attr.get("value_id") or "").strip().lower()
    value_name = str(attr.get("value_name") or "").strip().lower()
    return value_id in _BOOL_YES or value_name in _BOOL_YES


def _attr_float(item: dict[str, Any], attr_id: str) -> float | None:
    attr = _find_attr(item, attr_id)
    if not attr:
        return None
    struct = attr.get("value_struct") or {}
    if isinstance(struct, dict) and struct.get("number") is not None:
        try:
            return float(struct["number"])
        except (TypeError, ValueError):
            return None
    return _parse_leading_number(attr.get("value_name"))


def _attr_int(item: dict[str, Any], attr_id: str) -> int | None:
    value = _attr_float(item, attr_id)
    if value is None:
        return None
    return int(value)


def _parse_leading_number(raw: Any) -> float | None:
    if raw is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(raw))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", "."))
    except ValueError:
        return None


def _nested_name(item: dict[str, Any], *keys: str) -> str | None:
    cur: Any = item
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, dict):
        name = cur.get("name")
        return str(name).strip() if name else None
    if isinstance(cur, str) and cur.strip():
        return cur.strip()
    return None


def _url(item: dict[str, Any], external_id: str) -> str:
    permalink = str(item.get("permalink") or "").strip()
    if permalink:
        return permalink
    return f"https://inmueble.mercadolibre.com.ar/{external_id}"


def _photos(item: dict[str, Any]) -> list[str]:
    photos: list[str] = []
    thumb = item.get("thumbnail")
    if isinstance(thumb, str) and thumb:
        photos.append(thumb)
    for pic in item.get("pictures") or []:
        if not isinstance(pic, dict):
            continue
        url = pic.get("secure_url") or pic.get("url")
        if isinstance(url, str) and url and url not in photos:
            photos.append(url)
    return photos


def _coord(item: dict[str, Any], key: str) -> float | None:
    location = item.get("location") or {}
    if isinstance(location, dict) and location.get(key) not in (None, ""):
        try:
            return float(location[key])
        except (TypeError, ValueError):
            return None
    return None


def _parse_dt(raw: Any) -> datetime | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _raw_hash(item: dict[str, Any]) -> str:
    payload = {
        "id": item.get("id"),
        "price": item.get("price"),
        "currency_id": item.get("currency_id"),
        "title": item.get("title"),
        "attributes": item.get("attributes") or [],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
