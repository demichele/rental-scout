"""Adapter Argenprop. Parser de fixture HTML/JSON; fetch HTTP opcional.

Argenprop no publica API de búsqueda. robots.txt bloquea `/api/`.
La SERP pública es HTML (`/casas/alquiler/general-pacheco`); la paginación
permitida es `?pagina-1`..`?pagina-3`. Este adapter parsea cards
`.listing__item` / `a[data-item-card]`. No usa `/api/` ni api.sosiva451.com.
No burla captchas ni WAF (AWS; a veces HTTP 202 + gokuProps).

Los ToS prohíben scraping. El camino principal es la fixture guardada a
mano. `fetch_listings` es opcional: solo SERP HTML permitida por robots.txt.

Cómo guardar la fixture a mano (si el GET está bloqueado):

    1. En un navegador normal abrí
       https://www.argenprop.com/casas/alquiler/general-pacheco
    2. Si aparece captcha, challenge o WAF, resolvelo vos. No lo hagas desde acá.
    3. Archivo → Guardar como → Sólo HTML, a tests/fixtures/argenprop.html
    4. Con los `.listing__item` (o `a[data-item-card]`) de la SERP alcanza.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

import httpx

from src.models import Listing
from src.prefs import Prefs, load_prefs

log = logging.getLogger(__name__)

BASE_URL = "https://www.argenprop.com"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
USER_AGENT = "rental-scout personal"
REQUEST_TIMEOUT = 20.0
PAGE_DELAY_SEC = 1.0
MAX_PAGES = 3

# SERP HTML. robots.txt permite pág. 1 y ?pagina-1..3; no /api/.
SEARCH_PATH = "/casas/alquiler/general-pacheco"

_HOUSE_TYPES = {"casa", "house", "chalet", "dúplex", "duplex", "tríplex", "triplex", "cabaña", "cabana"}
_NOT_HOUSE = {"departamento", "depto", "apartamento", "apartment", "ph", "local", "oficina", "terreno"}
_GATED_RE = re.compile(
    r"barrio\s+(?:privado|cerrado)|country\s+club|\bcountry\b|acceso\s+controlado",
    re.IGNORECASE,
)
_POOL_RE = re.compile(r"\b(?:pileta|piscina)\b", re.IGNORECASE)
_PACHECO_RE = re.compile(r"pacheco", re.IGNORECASE)
_DORM_RE = re.compile(r"(\d+)\s*(?:dorm\.?|dormitorio)", re.IGNORECASE)
_AMB_RE = re.compile(r"(\d+)\s*(?:amb\.?|ambiente)", re.IGNORECASE)
_BATH_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(?:baño)", re.IGNORECASE)
_M2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m[²2]", re.IGNORECASE)
_BLOCKED_RE = re.compile(
    r"just a moment|attention required|cf-challenge|cf-mitigated|access denied|gokuprops|aws.?waf",
    re.IGNORECASE,
)
_CLASS_FIELDS = (
    ("card__title--primary", "title_primary"),
    ("card__title", "title"),
    ("card__address", "address"),
    ("card__info", "description"),
    ("card__price", "price_text"),
    ("card__currency", "currency"),
    ("card__expenses", "expenses_text"),
)
_VOID_TAGS = {"img", "br", "hr", "input", "meta", "link", "source"}


class FetchNotAllowed(RuntimeError):
    """robots.txt, WAF o captcha: no se pega a Argenprop."""


class _Capture:
    __slots__ = ("field", "depth", "parts")

    def __init__(self, field: str) -> None:
        self.field = field
        self.depth = 1
        self.parts: list[str] = []


class _SerpParser(HTMLParser):
    """Extrae cards `.listing__item` o `a[data-item-card]` de la SERP."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.cards: list[dict[str, Any]] = []
        self._card: dict[str, Any] | None = None
        self._card_tag: str | None = None
        self._card_depth = 0
        self._captures: list[_Capture] = []
        self._in_features = False
        self._features_depth = 0
        self._in_li = False
        self._li_depth = 0
        self._li_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = {key: (val or "") for key, val in attrs}
        classes = ad.get("class", "").split()

        if self._card is None:
            if tag == "div" and "listing__item" in classes:
                self._open_card("div")
            elif tag == "a" and ad.get("data-item-card"):
                self._open_card("a")
                self._apply_anchor(ad)
            else:
                return
        elif tag == self._card_tag or (self._card_tag == "div" and tag == "div"):
            self._card_depth += 1

        if self._card is None:
            return

        if tag == "a":
            self._apply_anchor(ad)
        if tag == "img":
            src = ad.get("src") or ad.get("data-src") or ""
            if src and src not in self._card["photos"]:
                self._card["photos"].append(src)

        if tag not in _VOID_TAGS:
            for cap in self._captures:
                cap.depth += 1
            field = _field_for_classes(classes)
            if field:
                self._captures.append(_Capture(field))

            if "card__main-features" in classes:
                self._in_features = True
                self._features_depth = 1
            elif self._in_features:
                if tag in {"ul", "ol", "div"}:
                    self._features_depth += 1
                if tag == "li" and not self._in_li:
                    self._in_li = True
                    self._li_depth = 1
                    self._li_parts = []
                elif self._in_li:
                    self._li_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if self._card is None:
            return

        if self._in_li:
            self._li_depth -= 1
            if self._li_depth <= 0:
                text = _squash("".join(self._li_parts))
                if text:
                    self._card["features"].append(text)
                self._in_li = False
                self._li_parts = []

        if self._in_features and tag in {"ul", "ol", "div"}:
            self._features_depth -= 1
            if self._features_depth <= 0:
                self._in_features = False

        still: list[_Capture] = []
        for cap in self._captures:
            cap.depth -= 1
            if cap.depth <= 0:
                text = _squash("".join(cap.parts))
                if text and not self._card.get(cap.field):
                    self._card[cap.field] = text
            else:
                still.append(cap)
        self._captures = still

        if tag == self._card_tag or (self._card_tag == "div" and tag == "div"):
            self._card_depth -= 1
            if self._card_depth <= 0:
                self.cards.append(self._card)
                self._card = None
                self._card_tag = None
                self._captures = []
                self._in_features = False
                self._in_li = False

    def handle_data(self, data: str) -> None:
        if self._card is None:
            return
        for cap in self._captures:
            cap.parts.append(data)
        if self._in_li:
            self._li_parts.append(data)

    def _open_card(self, tag: str) -> None:
        self._card_tag = tag
        self._card_depth = 1
        self._card = {
            "id": "",
            "url": "",
            "title": "",
            "title_primary": "",
            "address": "",
            "description": "",
            "price_text": "",
            "currency": "",
            "expenses_text": "",
            "features": [],
            "photos": [],
        }

    def _apply_anchor(self, ad: dict[str, str]) -> None:
        if self._card is None:
            return
        item_id = ad.get("data-item-card", "").strip()
        if item_id:
            self._card["id"] = item_id
        href = ad.get("href", "").strip()
        if href and not self._card["url"]:
            self._card["url"] = href


def search_url(*, page: int = 1) -> str:
    """URL de SERP HTML permitida (página 1, o ?pagina-2 / ?pagina-3)."""
    if page <= 1:
        return f"{BASE_URL}{SEARCH_PATH}"
    return f"{BASE_URL}{SEARCH_PATH}?pagina-{page}"


def fetch_listings(
    prefs: Prefs | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[Listing]:
    """GET de la SERP HTML si robots.txt lo permite. No burla WAF/captcha."""
    prefs = prefs or load_prefs()
    items = _fetch_search(client=client)
    return listings_from_search({"listings": items}, prefs)


def listings_from_html(html: str, prefs: Prefs | None = None) -> list[Listing]:
    """Mapea HTML de SERP (o JSON crudo) a Listing[] (sin red)."""
    text = html.strip()
    if text.startswith("{") or text.startswith("["):
        data = json.loads(text)
        if isinstance(data, list):
            return listings_from_search({"listings": data}, prefs)
        if not isinstance(data, dict):
            raise ValueError("JSON de Argenprop no es un objeto")
        return listings_from_search(data, prefs)
    return listings_from_search({"listings": _parse_html_cards(html)}, prefs)


def listings_from_search(payload: dict[str, Any], prefs: Prefs | None = None) -> list[Listing]:
    """Mapea listings de SERP a Listing[] filtrados (sin red)."""
    prefs = prefs or load_prefs()
    listings: list[Listing] = []
    seen: set[str] = set()
    for item in _items(payload):
        if not isinstance(item, dict):
            continue
        listing = _map_item(item, prefs)
        if listing is None or listing.external_id in seen:
            continue
        seen.add(listing.external_id)
        listings.append(listing)
    return listings


def _fetch_search(*, client: httpx.Client | None) -> list[dict[str, Any]]:
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        robots_text = _load_robots(http)
        merged: list[dict[str, Any]] = []
        url = search_url(page=1)
        for page in range(1, MAX_PAGES + 1):
            if not _allowed_by_robots(robots_text, url):
                if page == 1:
                    raise FetchNotAllowed(f"robots.txt no permite {url}")
                break
            if page > 1:
                time.sleep(PAGE_DELAY_SEC)
            response = http.get(
                url,
                headers=_request_headers(),
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            )
            if _is_blocked(response):
                if page == 1:
                    raise FetchNotAllowed(
                        "Argenprop bloqueó el GET (WAF/captcha/HTTP). "
                        "No se burla el challenge; guardá tests/fixtures/argenprop.html a mano."
                    )
                log.warning("Argenprop WAF en página %s; se usan las %s anteriores", page, page - 1)
                break
            response.raise_for_status()
            html = response.text or ""
            try:
                cards = _parse_html_cards(html)
            except ValueError as exc:
                if page == 1:
                    raise FetchNotAllowed(
                        "la respuesta no trae listing cards "
                        "(WAF/JS; guardá tests/fixtures/argenprop.html a mano)"
                    ) from exc
                log.warning("Argenprop sin cards en página %s; se usan las anteriores", page)
                break
            merged.extend(cards)
            nxt = _next_page_url(html, page)
            if not nxt:
                break
            url = nxt
        return merged
    finally:
        if owned:
            http.close()


def _load_robots(http: httpx.Client) -> str:
    response = http.get(
        ROBOTS_URL,
        headers=_request_headers(),
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )
    if _is_blocked(response):
        raise FetchNotAllowed("robots.txt bloqueado (WAF/captcha/HTTP)")
    response.raise_for_status()
    return response.text


def _allowed_by_robots(robots_text: str, url: str) -> bool:
    parser = RobotFileParser()
    parser.parse(robots_text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _is_blocked(response: httpx.Response) -> bool:
    if response.status_code in {202, 401, 403, 429, 503}:
        return True
    mitigated = str(response.headers.get("cf-mitigated") or "").lower()
    if mitigated in {"challenge", "captcha"}:
        return True
    snippet = (response.text or "")[:8000]
    return bool(_BLOCKED_RE.search(snippet))


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9",
    }


def _parse_html_cards(html: str) -> list[dict[str, Any]]:
    parser = _SerpParser()
    parser.feed(html)
    parser.close()
    cards = [card for card in parser.cards if card.get("id")]
    if not cards:
        raise ValueError("no hay listing cards (guardá la SERP a mano en tests/fixtures/argenprop.html)")
    return cards


def _items(payload: dict[str, Any]) -> list[Any]:
    for key in ("listings", "results", "listPostings"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def _next_page_url(html: str, current_page: int) -> str | None:
    nxt = current_page + 1
    if nxt > MAX_PAGES:
        return None
    if re.search(rf"[?&]pagina-{nxt}(?:[\"'\s>]|$)", html):
        return search_url(page=nxt)
    return None


def _map_item(item: dict[str, Any], prefs: Prefs) -> Listing | None:
    external_id = str(item.get("id") or item.get("postingId") or "").strip()
    if not external_id:
        return None
    if not _is_house(item, prefs):
        return None
    if not _is_pacheco(item):
        return None

    bedrooms = _bedrooms(item)
    rooms = _rooms(item)
    if not _enough_bedrooms(bedrooms, rooms, prefs.min_bedrooms):
        return None

    is_gated = _is_gated(item)
    if prefs.gated_only and not is_gated:
        return None

    has_pool = _has_pool(item)
    if prefs.require_pool and not has_pool:
        return None

    currency, price = _price(item)
    price_usd = price if currency == "USD" else None
    if price_usd is not None and price_usd > prefs.watch_max_price_usd:
        return None

    barrio = _barrio(item)
    return Listing(
        source="argenprop",
        external_id=external_id,
        url=_url(item, external_id),
        title=_title(item, external_id),
        locality="General Pacheco",
        barrio_name=barrio or None,
        is_gated=is_gated,
        bedrooms=bedrooms,
        bathrooms=_bathrooms(item),
        m2=_m2(item),
        has_pool=has_pool,
        currency=currency,
        price=price,
        price_usd=price_usd,
        expenses=_expenses(item),
        property_type="house",
        published_at=None,
        photos=_photos(item),
        lat=None,
        lng=None,
        raw_hash=_raw_hash(item),
    )


def _is_house(item: dict[str, Any], prefs: Prefs) -> bool:
    wanted = (prefs.property_type or "house").lower()
    if wanted not in {"house", "casa"}:
        return False
    url = str(item.get("url") or "").lower()
    if any(token in url for token in ("/departamento", "/ph-", "/ph/")):
        return False
    declared = str(item.get("property_type") or item.get("realEstateType") or "").lower()
    if isinstance(item.get("realEstateType"), dict):
        declared = str(item["realEstateType"].get("name") or "").lower()
    if declared in _NOT_HOUSE:
        return False
    if declared in _HOUSE_TYPES or "/casa" in url:
        return True
    title = _title(item, "").lower()
    return "casa" in title and "departamento" not in title


def _enough_bedrooms(bedrooms: int | None, rooms: int | None, min_bedrooms: int) -> bool:
    if bedrooms is not None:
        return bedrooms >= min_bedrooms
    if rooms is not None:
        return rooms >= min_bedrooms + 1
    return False


def _is_pacheco(item: dict[str, Any]) -> bool:
    return bool(_PACHECO_RE.search(_evidence_blob(item)))


def _is_gated(item: dict[str, Any]) -> bool:
    return bool(_GATED_RE.search(_evidence_blob(item)))


def _has_pool(item: dict[str, Any]) -> bool:
    return bool(_POOL_RE.search(_evidence_blob(item)))


def _evidence_blob(item: dict[str, Any]) -> str:
    parts = [
        _title(item, ""),
        item.get("title_primary"),
        item.get("description"),
        item.get("address"),
        item.get("location"),
        item.get("barrio"),
    ]
    for feat in item.get("features") or []:
        parts.append(feat)
    return " ".join(str(p) for p in parts if p)


def _barrio(item: dict[str, Any]) -> str | None:
    address = str(item.get("address") or "").strip()
    if address:
        return address
    primary = str(item.get("title_primary") or "").strip()
    if primary:
        # "Casa en Alquiler en Villa Pacheco, Tigre"
        match = re.search(r"en\s+(.+?)(?:,|$)", primary, re.IGNORECASE)
        if match:
            return match.group(1).strip() or None
    loc = item.get("location")
    if isinstance(loc, str) and loc.strip():
        return loc.strip()
    return None


def _bedrooms(item: dict[str, Any]) -> int | None:
    if item.get("bedrooms") is not None:
        parsed = _parse_leading_number(item.get("bedrooms"))
        return int(parsed) if parsed is not None else None
    blob = _feature_blob(item)
    match = _DORM_RE.search(blob)
    if match:
        return int(match.group(1))
    return None


def _rooms(item: dict[str, Any]) -> int | None:
    if item.get("rooms") is not None:
        parsed = _parse_leading_number(item.get("rooms"))
        return int(parsed) if parsed is not None else None
    blob = _feature_blob(item)
    match = _AMB_RE.search(blob)
    if match:
        return int(match.group(1))
    return None


def _bathrooms(item: dict[str, Any]) -> float | None:
    if item.get("bathrooms") is not None:
        return _parse_leading_number(item.get("bathrooms"))
    match = _BATH_RE.search(_feature_blob(item))
    if match:
        return _parse_leading_number(match.group(1))
    return None


def _m2(item: dict[str, Any]) -> float | None:
    if item.get("m2") is not None:
        return _parse_leading_number(item.get("m2"))
    match = _M2_RE.search(_feature_blob(item))
    if match:
        return _parse_leading_number(match.group(1))
    return None


def _feature_blob(item: dict[str, Any]) -> str:
    parts = list(item.get("features") or [])
    parts.append(item.get("description") or "")
    return " ".join(str(p) for p in parts if p)


def _price(item: dict[str, Any]) -> tuple[str, float]:
    if item.get("price") is not None:
        try:
            amount = float(item["price"])
        except (TypeError, ValueError):
            amount = 0.0
        currency = _normalize_currency(item.get("currency") or item.get("currency_id"))
        return currency, amount
    price_text = str(item.get("price_text") or "")
    currency = _normalize_currency(item.get("currency"))
    if currency in {"", "USD"} and price_text:
        inferred = _currency_from_text(price_text)
        if inferred:
            currency = inferred
    amount = _parse_locale_number(price_text) or 0.0
    return currency or "USD", amount


def _currency_from_text(raw: str) -> str | None:
    text = raw.upper()
    if any(token in text for token in ("USD", "U$S", "US$", "U$D")):
        return "USD"
    if "$" in raw:
        return "ARS"
    return None


def _normalize_currency(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if text in {"USD", "U$S", "US$", "U$D"}:
        return "USD"
    if text in {"ARS", "$", "AR$"}:
        return "ARS"
    return text or "USD"


def _expenses(item: dict[str, Any]) -> float | None:
    if item.get("expenses") is not None:
        try:
            return float(item["expenses"])
        except (TypeError, ValueError):
            return None
    text = str(item.get("expenses_text") or "")
    if "expensa" not in text.lower():
        return None
    return _parse_locale_number(text)


def _title(item: dict[str, Any], fallback: str) -> str:
    title = str(item.get("title") or item.get("title_primary") or "").strip()
    return title or fallback


def _url(item: dict[str, Any], external_id: str) -> str:
    permalink = str(item.get("url") or item.get("permalink") or "").strip()
    if permalink.startswith("http"):
        return permalink
    if permalink.startswith("/"):
        return urljoin(BASE_URL, permalink)
    if permalink:
        return f"{BASE_URL}/{permalink}"
    return f"{BASE_URL}/casa-en-alquiler--{external_id}"


def _photos(item: dict[str, Any]) -> list[str]:
    photos: list[str] = []
    pictures = item.get("photos") or item.get("pictures") or []
    for pic in pictures:
        if isinstance(pic, str) and pic:
            if pic not in photos:
                photos.append(pic)
            continue
        if not isinstance(pic, dict):
            continue
        url = pic.get("url") or pic.get("src")
        if isinstance(url, str) and url and url not in photos:
            photos.append(url)
    return photos


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


def _parse_locale_number(raw: Any) -> float | None:
    if raw is None:
        return None
    match = re.search(r"\d[\d.,]*", str(raw))
    if not match:
        return None
    num = match.group(0)
    if "," in num and "." in num:
        if num.rfind(",") > num.rfind("."):
            num = num.replace(".", "").replace(",", ".")
        else:
            num = num.replace(",", "")
    elif "," in num:
        parts = num.split(",")
        num = num.replace(",", "") if len(parts[-1]) == 3 else num.replace(",", ".")
    elif "." in num:
        parts = num.split(".")
        if len(parts[-1]) == 3:
            num = num.replace(".", "")
    try:
        return float(num)
    except ValueError:
        return None


def _field_for_classes(classes: list[str]) -> str | None:
    for cls, field in _CLASS_FIELDS:
        if cls in classes:
            return field
    return None


def _squash(text: str) -> str:
    return " ".join(text.split())


def _raw_hash(item: dict[str, Any]) -> str:
    payload = {
        "id": item.get("id") or item.get("postingId"),
        "title": item.get("title") or item.get("title_primary"),
        "price": item.get("price") or item.get("price_text"),
        "currency": item.get("currency"),
        "features": item.get("features") or [],
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
