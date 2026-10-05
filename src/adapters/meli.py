"""Adapter Mercado Libre Inmuebles. Parser de fixture HTML; fetch HTTP opcional.

La API `/sites/MLA/search` responde 403 (búsqueda pública cerrada) aun con
token válido. La SERP HTML de inmuebles sí responde; robots.txt permite la
página 1 de `/casas/alquiler/.../general-pacheco/` y bloquea `/*_Desde_` y
`/api/`. Este adapter parsea cards `.poly-card__content` (y JSON-LD si no
hay cards). No usa la API de search ni paginación `_Desde_`. No burla WAF
ni captcha.

`jobs.ping_meli` / `check_connection` siguen pegándole a la API (token),
aparte del scout.

Cómo guardar la fixture a mano (si el GET está bloqueado):

1. En un navegador normal abrí
   https://inmuebles.mercadolibre.com.ar/casas/alquiler/bsas-gba-norte/tigre/general-pacheco/
2. Si aparece captcha o challenge, resolvelo vos. No lo hagas desde acá.
3. Archivo → Guardar como → Sólo HTML, a tests/fixtures/meli.html
4. Con los `.poly-card__content` de la SERP alcanza.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from src.models import Listing
from src.prefs import Prefs, load_prefs

log = logging.getLogger(__name__)

SITE_ID = "MLA"
SEARCH_URL = f"https://api.mercadolibre.com/sites/{SITE_ID}/search"
USERS_ME_URL = "https://api.mercadolibre.com/users/me"
CATEGORY_HOUSES_RENT = "MLA1467"
CITY_TIGRE = "TUxBQ1RJRzk0ZjQw"
BASE_URL = "https://inmuebles.mercadolibre.com.ar"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
SEARCH_PATH = "/casas/alquiler/bsas-gba-norte/tigre/general-pacheco/"
USER_AGENT = "rental-scout personal"
REQUEST_TIMEOUT = 20.0

_BOOL_YES = {"sí", "si", "yes", "true", "242085"}
_HOUSE_TYPES = {
    "casa",
    "casas",
    "house",
    "chalet",
    "dúplex",
    "duplex",
    "tríplex",
    "triplex",
    "cabaña",
    "cabana",
}
_NOT_HOUSE = {"departamento", "depto", "apartamento", "apartment", "ph", "local", "oficina", "terreno"}
_GATED_RE = re.compile(
    r"barrio\s+(?:privado|cerrado)|country\s+club|\bcountry\b|golf\s+club|acceso\s+controlado",
    re.IGNORECASE,
)
_POOL_RE = re.compile(r"\b(?:pileta|piscina)\b", re.IGNORECASE)
_PACHECO_RE = re.compile(r"pacheco", re.IGNORECASE)
_ID_RE = re.compile(r"MLA-(\d+)", re.IGNORECASE)
_DORM_RE = re.compile(r"(\d+)\s*(?:dorm\.?|dormitorio)", re.IGNORECASE)
_AMB_RE = re.compile(r"(\d+)\s*(?:amb\.?|ambiente)", re.IGNORECASE)
_BATH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:baño)", re.IGNORECASE)
_M2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2]", re.IGNORECASE)
_BLOCKED_RE = re.compile(
    r"just a moment|attention required|cf-challenge|cf-mitigated|access denied|captcha",
    re.IGNORECASE,
)
_LD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_VOID_TAGS = {"img", "br", "hr", "input", "meta", "link", "source"}


class FetchNotAllowed(RuntimeError):
    """robots.txt, WAF o captcha: no se pega a Mercado Libre Inmuebles."""


class _Capture:
    __slots__ = ("field", "depth", "parts")

    def __init__(self, field: str) -> None:
        self.field = field
        self.depth = 1
        self.parts: list[str] = []


class _PolyParser(HTMLParser):
    """Extrae cards `.poly-card__content` de la SERP de inmuebles."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._card: dict[str, Any] | None = None
        self._card_depth = 0
        self._captures: list[_Capture] = []
        self._in_li = False
        self._li_depth = 0
        self._li_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {key: (val or "") for key, val in attrs}
        classes = ad.get("class", "").split()
        if self._card is None:
            if tag == "div" and "poly-card__content" in classes:
                self._card = {"features": []}
                self._card_depth = 1
            return
        self._card_depth += 1
        if tag == "a" and "poly-component__title" in classes:
            href = ad.get("href") or ""
            if href:
                self._card["permalink"] = href.split("#")[0]
        field = None
        if "poly-component__title" in classes:
            field = "title"
        elif "poly-component__headline" in classes:
            field = "headline"
        elif "poly-component__location" in classes:
            field = "location"
        elif "andes-money-amount__currency-symbol" in classes:
            field = "currency"
        elif "andes-money-amount__fraction" in classes:
            field = "price_text"
        if field:
            self._captures.append(_Capture(field))
        if tag == "li" and "poly-attributes_list__item" in classes:
            self._in_li = True
            self._li_depth = 1
            self._li_parts = []
        elif self._in_li:
            self._li_depth += 1
        if tag in _VOID_TAGS and self._card is not None:
            self._card_depth -= 1

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return
        if tag in _VOID_TAGS:
            return
        if self._in_li:
            self._li_depth -= 1
            if self._li_depth <= 0:
                text = " ".join(self._li_parts).strip()
                if text:
                    self._card["features"].append(text)
                self._in_li = False
                self._li_parts = []
        still: list[_Capture] = []
        for cap in self._captures:
            cap.depth -= 1
            if cap.depth <= 0:
                text = " ".join(cap.parts).strip()
                if text:
                    self._card[cap.field] = text
            else:
                still.append(cap)
        self._captures = still
        self._card_depth -= 1
        if self._card_depth <= 0:
            self.cards.append(self._card)
            self._card = None
            self._captures = []
            self._in_li = False

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if not text or self._card is None:
            return
        if self._in_li:
            self._li_parts.append(text)
        for cap in self._captures:
            cap.parts.append(text)


def search_url(*, page: int = 1) -> str:
    """SERP HTML de casas en alquiler en General Pacheco. Solo pág. 1 está permitida."""
    if page <= 1:
        return urljoin(BASE_URL, SEARCH_PATH)
    return urljoin(BASE_URL, f"{SEARCH_PATH.rstrip('/')}/_Desde_{48 * (page - 1) + 1}")


def fetch_listings(
    prefs: Prefs | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[Listing]:
    """GET de la SERP HTML si robots.txt lo permite. No burla WAF/captcha."""
    prefs = prefs or load_prefs()
    payload = _fetch_search(client=client)
    return listings_from_search(payload, prefs)


def listings_from_html(html: str, prefs: Prefs | None = None) -> list[Listing]:
    """Mapea SERP HTML (poly-cards / JSON-LD) a Listing[] (sin red)."""
    return listings_from_search({"results": _items_from_html(html)}, prefs)


def listings_from_search(payload: dict[str, Any], prefs: Prefs | None = None) -> list[Listing]:
    """Mapea results[] (API o cards HTML) a Listing[] filtrados (sin red)."""
    prefs = prefs or load_prefs()
    listings: list[Listing] = []
    seen: set[str] = set()
    skipped: Counter[str] = Counter()
    raw = payload.get("results") or []
    for item in raw:
        if not isinstance(item, dict):
            skipped["not_object"] += 1
            continue
        listing = _map_item(item, prefs)
        if listing is None:
            skipped["dropped"] += 1
            continue
        if listing.external_id in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(listing.external_id)
        listings.append(listing)
    log.info("meli mapped %s listings from %s results", len(listings), len(raw))
    if skipped:
        log.info(
            "meli skipped %s",
            " ".join(f"{name}={count}" for name, count in skipped.most_common()),
        )
    return listings


def _fetch_search(*, client: httpx.Client | None) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        robots_text = _load_robots(http)
        url = search_url()
        if not _allowed_by_robots(robots_text, url):
            raise FetchNotAllowed(f"robots.txt no permite {url}")
        response = http.get(
            url,
            headers=_html_headers(),
            timeout=REQUEST_TIMEOUT,
            follow_redirects=True,
        )
        if _is_blocked(response):
            raise FetchNotAllowed("Mercado Libre bloqueó el GET (WAF/captcha/HTTP)")
        response.raise_for_status()
        items = _items_from_html(response.text)
        if not items:
            raise FetchNotAllowed(
                "la SERP no trae poly-cards ni JSON-LD "
                "(WAF/JS; guardá tests/fixtures/meli.html a mano)"
            )
        return {"site_id": SITE_ID, "results": items}
    finally:
        if owned:
            http.close()


def _load_robots(http: httpx.Client) -> str:
    response = http.get(ROBOTS_URL, headers=_html_headers(), timeout=REQUEST_TIMEOUT)
    if _is_blocked(response):
        raise FetchNotAllowed("robots.txt bloqueado (WAF/captcha/HTTP)")
    response.raise_for_status()
    return response.text


def _allowed_by_robots(robots_text: str, url: str) -> bool:
    path = urlparse(url).path or "/"
    if "_Desde_" in path or "/api/" in path:
        return False
    parser = RobotFileParser()
    parser.parse(robots_text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _is_blocked(response: httpx.Response) -> bool:
    if response.status_code in {401, 403, 429, 503}:
        return True
    mitigated = str(response.headers.get("cf-mitigated") or "").lower()
    if mitigated in {"challenge", "captcha"}:
        return True
    snippet = (response.text or "")[:8000]
    return bool(_BLOCKED_RE.search(snippet))


def _html_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9",
    }


def _items_from_html(html: str) -> list[dict[str, Any]]:
    parser = _PolyParser()
    parser.feed(html)
    parser.close()
    cards = [_card_to_item(card) for card in parser.cards]
    cards = [item for item in cards if item.get("id")]
    if cards:
        return cards
    return _items_from_jsonld(html)


def _card_to_item(card: dict[str, Any]) -> dict[str, Any]:
    permalink = str(card.get("permalink") or "").strip()
    external_id = _id_from_url(permalink)
    title = str(card.get("title") or "").strip()
    headline = str(card.get("headline") or "").strip()
    location = str(card.get("location") or "").strip()
    currency = _currency_from_symbol(str(card.get("currency") or ""))
    price = _parse_price_text(str(card.get("price_text") or ""))
    parts = [p.strip() for p in location.split(",") if p.strip()]
    neighborhood = parts[0] if parts else ""
    city = parts[1] if len(parts) > 1 else ""
    prop = "Departamento" if _NOT_HOUSE.intersection(headline.lower().split()) or (
        "departamento" in headline.lower()
    ) else "Casa"
    if "departamento" in title.lower() or "depto" in title.lower():
        prop = "Departamento"
    attributes: list[dict[str, Any]] = [
        {"id": "PROPERTY_TYPE", "value_name": prop},
    ]
    for feature in card.get("features") or []:
        attributes.extend(_attrs_from_feature(str(feature)))
    feat = " ".join(str(x) for x in (card.get("features") or []))
    blob = f"{title} {headline} {location} {feat}"
    if _GATED_RE.search(blob):
        attributes.append({"id": "IN_GATED_COMMUNITY", "value_id": "242085", "value_name": "Sí"})
    if _POOL_RE.search(blob):
        attributes.append({"id": "HAS_SWIMMING_POOL", "value_id": "242085", "value_name": "Sí"})
    return {
        "id": external_id,
        "title": title or external_id,
        "price": price or 0,
        "currency_id": currency,
        "permalink": permalink,
        "category_id": "MLA1473" if prop == "Departamento" else CATEGORY_HOUSES_RENT,
        "location": {
            "neighborhood": {"name": neighborhood},
            "city": {"name": city},
            "address_line": location,
        },
        "attributes": attributes,
    }


def _attrs_from_feature(feature: str) -> list[dict[str, Any]]:
    attrs: list[dict[str, Any]] = []
    dorm = _DORM_RE.search(feature)
    if dorm:
        attrs.append({"id": "BEDROOMS", "value_name": dorm.group(1)})
    amb = _AMB_RE.search(feature)
    if amb:
        attrs.append({"id": "ROOMS", "value_name": amb.group(1)})
    bath = _BATH_RE.search(feature)
    if bath:
        attrs.append({"id": "FULL_BATHROOMS", "value_name": bath.group(1)})
    m2 = _M2_RE.search(feature)
    if m2:
        number = _parse_leading_number(m2.group(1))
        attrs.append(
            {
                "id": "COVERED_AREA",
                "value_name": feature.strip(),
                "value_struct": {"number": number, "unit": "m²"} if number is not None else None,
            }
        )
    return attrs


def _items_from_jsonld(html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for match in _LD_RE.finditer(html):
        try:
            payload = json.loads(match.group(1))
        except ValueError:
            continue
        graph = payload.get("@graph") if isinstance(payload, dict) else None
        nodes = graph if isinstance(graph, list) else [payload]
        for node in nodes:
            if not isinstance(node, dict):
                continue
            types = node.get("@type")
            type_names = types if isinstance(types, list) else [types]
            if "RealEstateListing" not in {str(t) for t in type_names}:
                continue
            item = _jsonld_to_item(node)
            if not item.get("id") or item["id"] in seen:
                continue
            seen.add(item["id"])
            items.append(item)
    return items


def _jsonld_to_item(node: dict[str, Any]) -> dict[str, Any]:
    offers = node.get("offers") if isinstance(node.get("offers"), dict) else {}
    address = node.get("address") if isinstance(node.get("address"), dict) else {}
    floor = node.get("floorSize") if isinstance(node.get("floorSize"), dict) else {}
    permalink = str(offers.get("url") or node.get("mainEntityOfPage") or "").split("#")[0]
    external_id = _id_from_url(permalink)
    title = str(node.get("name") or "").strip()
    currency = str(offers.get("priceCurrency") or "ARS").upper()
    try:
        price = float(offers.get("price") or 0)
    except (TypeError, ValueError):
        price = 0.0
    rooms = node.get("numberOfRooms")
    attributes: list[dict[str, Any]] = [
        {"id": "PROPERTY_TYPE", "value_name": "Casa"},
    ]
    if rooms is not None:
        attributes.append({"id": "ROOMS", "value_name": str(rooms)})
    if floor.get("value") is not None:
        attributes.append(
            {
                "id": "COVERED_AREA",
                "value_name": str(floor.get("value")),
                "value_struct": {"number": floor.get("value"), "unit": "m²"},
            }
        )
    image = node.get("image")
    blob = f"{title} {address.get('addressLocality') or ''}"
    if _GATED_RE.search(blob):
        attributes.append({"id": "IN_GATED_COMMUNITY", "value_id": "242085", "value_name": "Sí"})
    if _POOL_RE.search(blob):
        attributes.append({"id": "HAS_SWIMMING_POOL", "value_id": "242085", "value_name": "Sí"})
    if "departamento" in title.lower():
        attributes[0]["value_name"] = "Departamento"
    return {
        "id": external_id,
        "title": title or external_id,
        "price": price,
        "currency_id": currency,
        "permalink": permalink,
        "thumbnail": image if isinstance(image, str) else None,
        "category_id": CATEGORY_HOUSES_RENT,
        "date_created": node.get("datePosted"),
        "location": {
            "neighborhood": {"name": str(address.get("addressLocality") or "")},
            "city": {"name": str(address.get("addressLocality") or "")},
            "address_line": str(address.get("streetAddress") or ""),
        },
        "attributes": attributes,
    }


def _id_from_url(url: str) -> str:
    match = _ID_RE.search(url)
    if match:
        return f"MLA{match.group(1)}"
    bare = re.search(r"(MLA\d{8,})", url, re.IGNORECASE)
    return bare.group(1).upper() if bare else ""


def _currency_from_symbol(symbol: str) -> str:
    text = symbol.strip().upper().replace(" ", "")
    if "US" in text or text == "USD":
        return "USD"
    return "ARS"


def _parse_price_text(text: str) -> float:
    digits = re.sub(r"[^\d]", "", text)
    if not digits:
        return 0.0
    try:
        return float(digits)
    except ValueError:
        return 0.0


def _meli_access_token() -> str:
    return os.environ.get("MELI_ACCESS_TOKEN", "").strip()


def _looks_like_user_token(token: str) -> bool:
    """El access_token de usuario es APP_USR-... / TG-...; el secret suele ser ~32 chars."""
    upper = token.upper()
    return upper.startswith(("APP_USR-", "TG-")) or len(token) >= 48


def _search_params(offset: int) -> dict[str, str | int]:
    return {
        "category": CATEGORY_HOUSES_RENT,
        "city": CITY_TIGRE,
        "limit": 1 if offset == 0 else 50,
        "offset": offset,
    }


def _api_headers() -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    token = _meli_access_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _response_error_summary(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("message") or data.get("error") or "").strip()


@dataclass(frozen=True)
class ConnectionCheck:
    """Resultado de validar el token MELI sin correr el scout."""

    ok: bool
    message: str
    user_id: str | None = None
    nickname: str | None = None
    search_http: int | None = None
    search_total: int | None = None
    search_error: str | None = None


def check_connection(*, client: httpx.Client | None = None) -> ConnectionCheck:
    """Valida el token (GET /users/me + search limit=1). Lo usa jobs.ping_meli, no fetch_listings."""
    token = _meli_access_token()
    if not token:
        return ConnectionCheck(
            ok=False,
            message=(
                "falta MELI_ACCESS_TOKEN en .env. Autorizá la app "
                "(oauth/README.md) y pegá el access_token APP_USR-..."
            ),
        )
    if not _looks_like_user_token(token):
        return ConnectionCheck(
            ok=False,
            message=(
                "MELI_ACCESS_TOKEN no parece access_token de usuario "
                "(tiene que empezar con APP_USR-). No uses el Client Secret."
            ),
        )

    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        me = http.get(
            USERS_ME_URL,
            headers=_api_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if me.status_code in {401, 403}:
            return ConnectionCheck(
                ok=False,
                message=(
                    f"Mercado Libre rechazó MELI_ACCESS_TOKEN en /users/me "
                    f"(HTTP {me.status_code}). Autorizá de nuevo y actualizá "
                    "MELI_ACCESS_TOKEN (oauth/README.md)."
                ),
            )
        me.raise_for_status()
        body: Any = {}
        try:
            parsed = me.json()
            if isinstance(parsed, dict):
                body = parsed
        except ValueError:
            body = {}
        user_id = str(body.get("id") or "").strip() or None
        nickname = str(body.get("nickname") or "").strip() or None

        search = http.get(
            SEARCH_URL,
            params=_search_params(0),
            headers=_api_headers(),
            timeout=REQUEST_TIMEOUT,
        )
        if search.status_code in {401, 403}:
            detail = _response_error_summary(search)
            return ConnectionCheck(
                ok=False,
                user_id=user_id,
                nickname=nickname,
                search_http=search.status_code,
                search_error=detail or None,
                message=(
                    f"token válido (/users/me) pero /sites/MLA/search está prohibido "
                    f"(HTTP {search.status_code}"
                    + (f" {detail}" if detail else "")
                    + "). El scout no usa esa API: busca la SERP HTML de inmuebles."
                ),
            )
        search.raise_for_status()
        payload: Any = {}
        try:
            parsed_search = search.json()
            if isinstance(parsed_search, dict):
                payload = parsed_search
        except ValueError:
            payload = {}
        paging = payload.get("paging") if isinstance(payload, dict) else {}
        total = None
        if isinstance(paging, dict) and paging.get("total") is not None:
            try:
                total = int(paging["total"])
            except (TypeError, ValueError):
                total = None
        return ConnectionCheck(
            ok=True,
            user_id=user_id,
            nickname=nickname,
            search_http=search.status_code,
            search_total=total,
            message="ok: Mercado Libre aceptó el token y la búsqueda API",
        )
    except httpx.HTTPError as exc:
        return ConnectionCheck(ok=False, message=f"error de red contra Mercado Libre: {exc}")
    finally:
        if owned:
            http.close()


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
    dashed = external_id.replace("MLA", "MLA-", 1) if external_id.startswith("MLA") else external_id
    return f"https://casa.mercadolibre.com.ar/{dashed}"


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
