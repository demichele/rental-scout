"""Adapter ZonaProp. Parser de fixture HTML/JSON; fetch HTTP opcional.

ZonaProp no publica API de búsqueda. La SERP incrusta
`window.__PRELOADED_STATE__` (script#preloadedData). Este adapter parsea
eso. No usa `/avisos-api/`, `/users-api/` ni APIs móviles: robots.txt las
bloquea o no son públicas. No burla captchas ni WAF.

Cómo guardar la fixture a mano (si el GET está bloqueado):

    1. En un navegador normal abrí una SERP de barrio, p.ej.
   https://www.zonaprop.com.ar/casas-alquiler-nordelta.html
   (el slug menos-de-N-dolares redirige 301 a la SERP sin tope).
   Venta: /casas-venta-tigre.html y /casas-venta-general-pacheco.html.
    2. Si aparece captcha o challenge, resolvelo vos. No lo hagas desde acá.
    3. Archivo → Guardar como → Sólo HTML, a tests/fixtures/zonaprop.html
    4. Con el <script> que contiene window.__PRELOADED_STATE__ alcanza.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from collections import Counter
from datetime import datetime
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from src.match import enough_size, fold_text, size_required, text_matches_barrios
from src.models import Listing
from src.prefs import Prefs, load_prefs

log = logging.getLogger(__name__)

BASE_URL = "https://www.zonaprop.com.ar"
ROBOTS_URL = f"{BASE_URL}/robots.txt"
USER_AGENT = "rental-scout personal"
REQUEST_TIMEOUT = 20.0
PAGE_DELAY_SEC = 1.0
MAX_PAGES = 5

# SERP HTML. robots.txt permite pág. 1 y pagina-2..5; no /avisos-api/.
SEARCH_PATH = "/casas-alquiler-nordelta.html"
SALE_SEARCH_PATH = "/casas-venta-tigre.html"

# Localidades de venta → slug ZonaProp. "Pacheco" es alias de match, no SERP.
_SALE_SEARCH_SLUGS: dict[str, str] = {
    "tigre": "tigre",
    "general pacheco": "general-pacheco",
}

# Slugs de ZonaProp por barrio (sin acento). Talar del Lago son dos countries
# (I y II); los slugs *-1 / *-2 redirigen 301 al SERP nacional.
_BARRIO_SEARCH_SLUGS: dict[str, tuple[str, ...]] = {
    "talar del lago": ("talar-del-lago-i", "talar-del-lago-ii"),
    "nordelta": ("nordelta",),
    "los alisos": ("los-alisos",),
    "la comarca": ("la-comarca",),
    "barrancas de santa maria": ("barrancas-de-santa-maria",),
    "barrancas de san jose": ("barrancas-de-san-jose",),
    "santa barbara": ("santa-barbara",),
}

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
_PRELOADED_RE = re.compile(r"window\.__PRELOADED_STATE__\s*=\s*")
_SERP_SLUG_RE = re.compile(
    r"^/casas-(?:alquiler|venta)-(.+?)(?:-pagina-\d+)?\.html$",
    re.IGNORECASE,
)
_BLOCKED_RE = re.compile(
    r"just a moment|attention required|cf-challenge|cf-mitigated|access denied",
    re.IGNORECASE,
)


class FetchNotAllowed(RuntimeError):
    """robots.txt, WAF o captcha: no se pega a ZonaProp."""


def search_paths(prefs: Prefs | None = None) -> list[str]:
    """SERPs HTML por barrio (alquiler) o localidad (venta)."""
    prefs = prefs or load_prefs()
    if prefs.operation == "sale":
        return _sale_search_paths(prefs)
    paths: list[str] = []
    seen: set[str] = set()
    for barrio in prefs.barrios:
        for slug in _BARRIO_SEARCH_SLUGS.get(fold_text(barrio), (_slugify(barrio),)):
            path = f"/casas-alquiler-{slug}.html"
            if path not in seen:
                seen.add(path)
                paths.append(path)
    return paths or [SEARCH_PATH]


def _sale_search_paths(prefs: Prefs) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    names = prefs.localities or ([prefs.locality] if prefs.locality else [])
    for name in names:
        slug = _SALE_SEARCH_SLUGS.get(fold_text(name))
        if not slug:
            continue
        path = f"/casas-venta-{slug}.html"
        if path not in seen:
            seen.add(path)
            paths.append(path)
    return paths or [SALE_SEARCH_PATH]


def search_url(
    *,
    watch_max_usd: int | None = None,
    page: int = 1,
    path: str | None = None,
    prefs: Prefs | None = None,
) -> str:
    """URL de SERP HTML permitida (página 1, o pagina-2..5).

    watch_max_usd se ignora: ZonaProp 301 el slug menos-de-N-dolares a la
    SERP sin tope. El cap queda en _map_item.
    """
    _ = watch_max_usd
    resolved = path or (search_paths(prefs)[0] if prefs is not None else SEARCH_PATH)
    slug = resolved.removeprefix("/").removesuffix(".html")
    if page <= 1:
        return f"{BASE_URL}/{slug}.html"
    return f"{BASE_URL}/{slug}-pagina-{page}.html"


def _slugify(barrio: str) -> str:
    folded = fold_text(barrio).strip()
    return re.sub(r"[^a-z0-9]+", "-", folded).strip("-")


def fetch_listings(
    prefs: Prefs | None = None,
    *,
    client: httpx.Client | None = None,
) -> list[Listing]:
    """GET de la SERP HTML si robots.txt lo permite. No burla WAF/captcha."""
    prefs = prefs or load_prefs()
    payload = _fetch_search(prefs, client=client)
    return listings_from_search(payload, prefs)


def listings_from_html(html: str, prefs: Prefs | None = None) -> list[Listing]:
    """Mapea HTML (o JSON crudo de __PRELOADED_STATE__) a Listing[] (sin red)."""
    return listings_from_search(_parse_preloaded(html), prefs)


def listings_from_search(payload: dict[str, Any], prefs: Prefs | None = None) -> list[Listing]:
    """Mapea listStore.listPostings a Listing[] filtrados (sin red)."""
    prefs = prefs or load_prefs()
    listings: list[Listing] = []
    seen: set[str] = set()
    skipped: Counter[str] = Counter()
    for item in _postings(payload):
        if not isinstance(item, dict):
            skipped["not_object"] += 1
            continue
        listing, reason = _try_map_item(item, prefs)
        if listing is None:
            skipped[reason or "dropped"] += 1
            continue
        if listing.external_id in seen:
            skipped["duplicate"] += 1
            continue
        seen.add(listing.external_id)
        listings.append(listing)
    log.info(
        "zonaprop mapped %s listings from %s postings",
        len(listings),
        len(_postings(payload)),
    )
    if skipped:
        log.info(
            "zonaprop skipped %s",
            " ".join(f"{name}={count}" for name, count in skipped.most_common()),
        )
    return listings


def _fetch_search(prefs: Prefs, *, client: httpx.Client | None) -> dict[str, Any]:
    owned = client is None
    http = client or httpx.Client(timeout=REQUEST_TIMEOUT)
    try:
        robots_text = _load_robots(http)
        merged: dict[str, Any] = {"listStore": {"listPostings": []}}
        any_ok = False
        last_error: FetchNotAllowed | None = None
        first_request = True
        for path in search_paths(prefs):
            url = search_url(path=path, watch_max_usd=prefs.watch_max_price_usd)
            path_ok = False
            for _page in range(MAX_PAGES):
                if not _allowed_by_robots(robots_text, url):
                    if _page == 0:
                        log.warning("robots.txt no permite %s; se salta", url)
                        break
                    break
                if owned and not first_request:
                    time.sleep(PAGE_DELAY_SEC)
                first_request = False
                response = http.get(
                    url,
                    headers=_request_headers(),
                    timeout=REQUEST_TIMEOUT,
                    follow_redirects=True,
                )
                if _off_barrio_redirect(url, response):
                    log.warning(
                        "ZonaProp redirigió %s a %s; se salta (otro barrio)",
                        url,
                        getattr(response, "url", ""),
                    )
                    break
                if response.status_code == 404:
                    log.warning("ZonaProp 404 en %s; se salta", url)
                    break
                if _is_blocked(response):
                    if _page == 0:
                        last_error = FetchNotAllowed(
                            "ZonaProp bloqueó el GET (WAF/captcha/HTTP)"
                        )
                        log.warning("%s: %s", url, last_error)
                        break
                    log.warning(
                        "ZonaProp WAF en %s página %s; se usan las %s anteriores",
                        path,
                        _page + 1,
                        _page,
                    )
                    break
                try:
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    if _page == 0:
                        last_error = FetchNotAllowed(f"HTTP en {url}: {exc}")
                        log.warning("%s", last_error)
                        break
                    log.warning("ZonaProp HTTP en %s página %s; se usan las anteriores", path, _page + 1)
                    break
                try:
                    payload = _parse_preloaded(response.text)
                except ValueError:
                    if _page == 0:
                        last_error = FetchNotAllowed(
                            "la respuesta no trae __PRELOADED_STATE__ "
                            "(WAF/JS; guardá tests/fixtures/zonaprop.html a mano)"
                        )
                        log.warning("%s: %s", url, last_error)
                        break
                    log.warning(
                        "ZonaProp sin PRELOADED_STATE en %s página %s; se usan las anteriores",
                        path,
                        _page + 1,
                    )
                    break
                if not path_ok and not any_ok:
                    merged = {**payload}
                    store = dict(merged.get("listStore") or {})
                    store["listPostings"] = []
                    merged["listStore"] = store
                (merged["listStore"]["listPostings"]).extend(_postings(payload))
                path_ok = True
                any_ok = True
                nxt = _next_page_url(payload)
                if not nxt:
                    break
                url = nxt
        if not any_ok:
            raise last_error or FetchNotAllowed("ZonaProp no devolvió ninguna SERP usable")
        return merged
    finally:
        if owned:
            http.close()


def _load_robots(http: httpx.Client) -> str:
    response = http.get(ROBOTS_URL, headers=_request_headers(), timeout=REQUEST_TIMEOUT)
    if _is_blocked(response):
        raise FetchNotAllowed("robots.txt bloqueado (WAF/captcha/HTTP)")
    response.raise_for_status()
    return response.text


def _allowed_by_robots(robots_text: str, url: str) -> bool:
    parser = RobotFileParser()
    parser.parse(robots_text.splitlines())
    return parser.can_fetch(USER_AGENT, url)


def _serp_barrio_slug(path: str) -> str | None:
    match = _SERP_SLUG_RE.match(path)
    return match.group(1).casefold() if match else None


def _response_path(response: httpx.Response) -> str:
    final = getattr(response, "url", None)
    if final is None:
        return ""
    path = getattr(final, "path", None)
    if isinstance(path, str) and path.startswith("/"):
        return path
    text = str(final)
    if "://" in text:
        return urlparse(text).path
    if text.startswith("/"):
        return text
    return ""


def _off_barrio_redirect(requested_url: str, response: httpx.Response) -> bool:
    """True si el 301/302 dejó el SERP del barrio (p.ej. talar-del-lago-1 → /casas-alquiler.html)."""
    requested_slug = _serp_barrio_slug(urlparse(requested_url).path)
    if not requested_slug:
        return False
    final_path = _response_path(response)
    if not final_path:
        return False
    return _serp_barrio_slug(final_path) != requested_slug


def _is_blocked(response: httpx.Response) -> bool:
    if response.status_code in {401, 403, 429, 503}:
        return True
    mitigated = str(response.headers.get("cf-mitigated") or "").lower()
    if mitigated in {"challenge", "captcha"}:
        return True
    snippet = (response.text or "")[:8000]
    return bool(_BLOCKED_RE.search(snippet))


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-AR,es;q=0.9",
    }


def _parse_preloaded(raw: str) -> dict[str, Any]:
    text = raw.strip()
    if text.startswith("{"):
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("JSON de ZonaProp no es un objeto")
        return data
    match = _PRELOADED_RE.search(raw)
    if not match:
        raise ValueError(
            "no hay window.__PRELOADED_STATE__ "
            "(guardá la SERP a mano en tests/fixtures/zonaprop.html)"
        )
    data, _end = json.JSONDecoder().raw_decode(raw, match.end())
    if not isinstance(data, dict):
        raise ValueError("PRELOADED_STATE no es un objeto")
    return data


def _postings(payload: dict[str, Any]) -> list[Any]:
    store = payload.get("listStore")
    if isinstance(store, dict) and isinstance(store.get("listPostings"), list):
        return store["listPostings"]
    if isinstance(payload.get("listPostings"), list):
        return payload["listPostings"]
    return []


def _next_page_url(payload: dict[str, Any]) -> str | None:
    store = payload.get("listStore") if isinstance(payload.get("listStore"), dict) else {}
    paging = store.get("paging") or payload.get("paging") or {}
    if not isinstance(paging, dict):
        return None
    pages = paging.get("pagesUrl") or {}
    nxt = pages.get("nextPage") if isinstance(pages, dict) else None
    if not isinstance(nxt, str) or not nxt.strip():
        return None
    return urljoin(BASE_URL + "/", nxt.strip())


def _map_item(item: dict[str, Any], prefs: Prefs) -> Listing | None:
    listing, _reason = _try_map_item(item, prefs)
    return listing


def _try_map_item(item: dict[str, Any], prefs: Prefs) -> tuple[Listing | None, str | None]:
    external_id = str(item.get("postingId") or item.get("id") or "").strip()
    if not external_id:
        return None, "no_id"
    if not _is_house(item, prefs):
        return None, "not_house"
    if prefs.barrios and not text_matches_barrios(_evidence_blob(item), prefs.barrios):
        return None, "wrong_barrio"
    if prefs.localities and not text_matches_barrios(_evidence_blob(item), prefs.localities):
        return None, "wrong_locality"

    bedrooms = _feature_int(item, "dormitorio", "habitacion", "habitación")
    rooms = _feature_int(item, "ambiente")
    if size_required(prefs) and not enough_size(bedrooms, rooms, prefs):
        return None, "bedrooms"

    is_gated = _is_gated(item)
    if prefs.gated_only and not is_gated:
        return None, "not_gated"

    has_pool = _has_pool(item)
    if prefs.require_pool and not has_pool:
        return None, "no_pool"

    currency, price = _price(item)
    price_usd = price if currency == "USD" else None
    if prefs.operation == "sale" and price_usd is None:
        return None, "no_usd"
    if price_usd is not None and price_usd > prefs.watch_max_price_usd:
        return None, "over_watch"
    if price_usd is not None and price_usd < prefs.min_price_usd:
        return None, "under_watch"

    loc = _posting_location(item)
    barrio = _nested_str(loc, "location", "name") if loc else None
    source = (prefs.listing_source or "zonaprop").strip() or "zonaprop"
    return Listing(
        source=source,
        external_id=external_id,
        operation=prefs.operation,
        url=_url(item, external_id),
        title=_title(item, external_id),
        locality=_locality_name(item, prefs),
        barrio_name=barrio or None,
        is_gated=is_gated,
        bedrooms=bedrooms,
        ambientes=rooms,
        bathrooms=_feature_float(item, "baño"),
        m2=_feature_float(item, "cubierta") or _feature_float(item, "superficie total"),
        has_pool=has_pool,
        currency=currency,
        price=price,
        price_usd=price_usd,
        expenses=_expenses(item),
        property_type="house",
        published_at=_parse_dt(item.get("modified_date") or item.get("modifiedDate")),
        photos=_photos(item),
        lat=_coord(item, "latitude"),
        lng=_coord(item, "longitude"),
        raw_hash=_raw_hash(item),
    ), None


def _is_house(item: dict[str, Any], prefs: Prefs) -> bool:
    wanted = (prefs.property_type or "house").lower()
    if wanted not in {"house", "casa"}:
        return False
    prop = (_nested_str(item, "realEstateType", "name") or "").lower()
    if _singular_type(prop) in _NOT_HOUSE or prop in _NOT_HOUSE:
        return False
    if prop in _HOUSE_TYPES or _singular_type(prop) in _HOUSE_TYPES:
        return True
    house = item.get("house")
    if isinstance(house, dict) and str(house.get("type") or "").lower() == "house":
        return True
    title = _title(item, "").lower()
    generated = str(item.get("generatedTitle") or "").lower()
    return ("casa" in title or generated.startswith("casa")) and "departamento" not in title


def _locality_name(item: dict[str, Any], prefs: Prefs | None = None) -> str:
    names = _location_names(item)
    if prefs and prefs.localities:
        for canonical in sorted(prefs.localities, key=lambda name: -len(name.strip())):
            folded = fold_text(canonical)
            if any(folded == fold_text(name) or folded in fold_text(name) for name in names):
                return canonical
    loc = _posting_location(item)
    node = loc.get("location") if loc else None
    if isinstance(node, dict):
        parent = node.get("parent")
        if isinstance(parent, dict):
            parent_name = str(parent.get("name") or "").strip()
            if parent_name:
                return parent_name
        leaf = str(node.get("name") or "").strip()
        if leaf:
            return leaf
    return "Tigre"


def _location_names(item: dict[str, Any]) -> list[str]:
    names: list[str] = []
    loc = _posting_location(item)
    node: Any = loc.get("location") if loc else None
    while isinstance(node, dict):
        name = str(node.get("name") or "").strip()
        if name:
            names.append(name)
        node = node.get("parent")
    return names


def _is_gated(item: dict[str, Any]) -> bool:
    return bool(_GATED_RE.search(_evidence_blob(item)))


def _has_pool(item: dict[str, Any]) -> bool:
    return bool(_POOL_RE.search(_evidence_blob(item)))


def _evidence_blob(item: dict[str, Any]) -> str:
    parts = [
        _title(item, ""),
        item.get("description"),
        item.get("descriptionNormalized"),
        item.get("generatedTitle"),
        _location_text(item),
    ]
    house = item.get("house")
    if isinstance(house, dict):
        parts.append(house.get("name"))
        addr = house.get("address")
        if isinstance(addr, dict):
            parts.append(addr.get("name"))
    for label, value in _iter_features(item):
        parts.append(label)
        parts.append(value)
    for extra in item.get("highlightedFeatures") or []:
        parts.append(extra)
    return " ".join(str(p) for p in parts if p)


def _singular_type(name: str) -> str:
    text = name.strip().lower()
    if text.endswith("s") and text[:-1] in _HOUSE_TYPES | _NOT_HOUSE:
        return text[:-1]
    return text


def _location_text(item: dict[str, Any]) -> str:
    loc = _posting_location(item)
    parts: list[str] = []
    if isinstance(loc, dict):
        address = loc.get("address") or {}
        if isinstance(address, dict):
            parts.append(str(address.get("name") or ""))
        node: Any = loc.get("location")
        while isinstance(node, dict):
            for key in ("name", "label", "shortLocation"):
                val = node.get(key)
                if val:
                    parts.append(str(val))
            node = node.get("parent")
    return " ".join(p for p in parts if p)


def _posting_location(item: dict[str, Any]) -> dict[str, Any] | None:
    loc = item.get("postingLocation")
    return loc if isinstance(loc, dict) else None


def _iter_features(item: dict[str, Any]) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    main = item.get("mainFeatures") or {}
    if isinstance(main, dict):
        for feat in main.values():
            if isinstance(feat, dict):
                found.append((str(feat.get("label") or ""), str(feat.get("value") or "")))
    general = item.get("generalFeatures") or {}
    if isinstance(general, dict):
        for group in general.values():
            if not isinstance(group, dict):
                continue
            for feat in group.values():
                if isinstance(feat, dict):
                    found.append((str(feat.get("label") or ""), str(feat.get("value") or "")))
    return found


def _feature_float(item: dict[str, Any], needle: str) -> float | None:
    needle_l = needle.lower()
    for label, value in _iter_features(item):
        if needle_l in label.lower():
            parsed = _parse_leading_number(value)
            if parsed is not None:
                return parsed
    return None


def _feature_int(item: dict[str, Any], *needles: str) -> int | None:
    needles_l = tuple(n.lower() for n in needles)
    for label, value in _iter_features(item):
        lab = label.lower()
        if any(n in lab for n in needles_l):
            parsed = _parse_leading_number(value)
            if parsed is not None:
                return int(parsed)
    return None


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


def _price(item: dict[str, Any]) -> tuple[str, float]:
    for op in item.get("priceOperationTypes") or []:
        if not isinstance(op, dict):
            continue
        for price in op.get("prices") or []:
            if not isinstance(price, dict) or price.get("amount") is None:
                continue
            try:
                amount = float(price["amount"])
            except (TypeError, ValueError):
                continue
            currency = _normalize_currency(price.get("currency") or price.get("currencyId"))
            return currency, amount
    return "USD", 0.0


_CURRENCY_IDS = {"1": "ARS", "2": "USD"}


def _normalize_currency(raw: Any) -> str:
    text = str(raw or "").strip().upper()
    if text in _CURRENCY_IDS:
        return _CURRENCY_IDS[text]
    if text in {"USD", "U$S", "US$", "U$D"}:
        return "USD"
    if text in {"ARS", "$", "AR$"}:
        return "ARS"
    return text or "USD"


def _expenses(item: dict[str, Any]) -> float | None:
    expenses = item.get("expenses")
    if not isinstance(expenses, dict) or expenses.get("amount") is None:
        return None
    try:
        return float(expenses["amount"])
    except (TypeError, ValueError):
        return None


def _title(item: dict[str, Any], fallback: str) -> str:
    title = str(item.get("title") or "").strip()
    return title or fallback


def _url(item: dict[str, Any], external_id: str) -> str:
    permalink = str(item.get("url") or item.get("permalink") or "").strip()
    if permalink.startswith("http"):
        return permalink
    if permalink.startswith("/"):
        return urljoin(BASE_URL, permalink)
    if permalink:
        return f"{BASE_URL}/{permalink}"
    return f"{BASE_URL}/propiedades/{external_id}.html"


def _photos(item: dict[str, Any]) -> list[str]:
    photos: list[str] = []
    visible = item.get("visiblePictures") or {}
    pictures = visible.get("pictures") if isinstance(visible, dict) else None
    if not isinstance(pictures, list):
        pictures = item.get("pictures") or []
    for pic in pictures:
        if isinstance(pic, str) and pic:
            if pic not in photos:
                photos.append(pic)
            continue
        if not isinstance(pic, dict):
            continue
        url = pic.get("url1200x1200") or pic.get("url730x532") or pic.get("url")
        if isinstance(url, str) and url and url not in photos:
            photos.append(url)
    return photos


def _coord(item: dict[str, Any], key: str) -> float | None:
    loc = _posting_location(item) or {}
    geo = loc.get("postingGeolocation") or {}
    point = geo.get("geolocation") if isinstance(geo, dict) else None
    if isinstance(point, dict) and point.get(key) not in (None, ""):
        try:
            return float(point[key])
        except (TypeError, ValueError):
            return None
    return None


def _nested_str(item: dict[str, Any], *keys: str) -> str | None:
    cur: Any = item
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    if isinstance(cur, str) and cur.strip():
        return cur.strip()
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
        "postingId": item.get("postingId") or item.get("id"),
        "title": item.get("title"),
        "priceOperationTypes": item.get("priceOperationTypes") or [],
        "mainFeatures": item.get("mainFeatures") or {},
        "generalFeatures": item.get("generalFeatures") or {},
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
