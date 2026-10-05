import unicodedata
from collections.abc import Sequence

from src.models import Listing
from src.prefs import Prefs

_HOUSE_TYPES = {"house", "casa"}


def fold_text(text: str) -> str:
    """minúsculas sin acentos: 'Santa Bárbara' → 'santa barbara'."""
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).casefold()


def text_matches_barrios(text: str, barrios: Sequence[str]) -> bool:
    blob = fold_text(text)
    if not blob:
        return False
    return any(fold_text(name) in blob for name in barrios if name and name.strip())


def listing_matches_barrios(listing: Listing, prefs: Prefs) -> bool:
    """Si hay `barrios`, el aviso tiene que nombrar uno. Si no, locality exacta."""
    if prefs.barrios:
        blob = " ".join(
            part for part in (listing.barrio_name, listing.locality, listing.title) if part
        )
        return text_matches_barrios(blob, prefs.barrios)
    return listing.locality.strip().casefold() == prefs.locality.strip().casefold()


def is_watch(listing: Listing, prefs: Prefs) -> bool:
    """True si el aviso se guarda: casa, barrio, gated, dorms/ambientes, pileta, USD ≤ watch_max."""
    if listing.price_usd is None:
        return False
    if listing.price_usd > prefs.watch_max_price_usd:
        return False
    if not _is_wanted_type(listing, prefs):
        return False
    if not listing_matches_barrios(listing, prefs):
        return False
    if prefs.gated_only and not listing.is_gated:
        return False
    if not _enough_bedrooms(listing, prefs):
        return False
    if prefs.require_pool and not listing.has_pool:
        return False
    return True


def is_match(listing: Listing, prefs: Prefs) -> bool:
    """WATCH + precio en el rango de notificación (NUEVA)."""
    if not is_watch(listing, prefs):
        return False
    price = listing.price_usd
    if price is None:
        return False
    return prefs.min_price_usd <= price <= prefs.max_price_usd


def is_drop(old_usd: float | None, new_usd: float | None, prefs: Prefs) -> bool:
    """Baja que notifica BAJÓ: umbral USD/% y precio nuevo dentro del rango (incluye entrar al rango)."""
    if old_usd is None or new_usd is None:
        return False
    if not (prefs.min_price_usd <= new_usd <= prefs.max_price_usd):
        return False
    drop = old_usd - new_usd
    if drop <= 0:
        return False
    threshold = max(float(prefs.min_drop_usd), prefs.min_drop_pct * old_usd)
    return drop >= threshold


def _is_wanted_type(listing: Listing, prefs: Prefs) -> bool:
    wanted = (prefs.property_type or "house").strip().casefold()
    actual = (listing.property_type or "").strip().casefold()
    if wanted in _HOUSE_TYPES:
        return actual in _HOUSE_TYPES
    return actual == wanted


def _enough_bedrooms(listing: Listing, prefs: Prefs) -> bool:
    if listing.bedrooms is not None:
        return listing.bedrooms >= prefs.min_bedrooms
    # 4 dorm ≈ 5+ ambientes. Listing no guarda ambientes; adapters ya filtraron ese fallback.
    return True
