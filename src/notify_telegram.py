from __future__ import annotations

import os

import httpx

from src.models import Listing
from src.prefs import Prefs, load_prefs

TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT = 20.0


class TelegramConfigError(RuntimeError):
    """Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."""


def send_new(listing: Listing) -> None:
    prefs = load_prefs()
    _send(_format_new(listing, prefs))


def send_price_drop(listing: Listing, old_usd: float, new_usd: float) -> None:
    prefs = load_prefs()
    _send(_format_price_drop(listing, old_usd, new_usd, prefs))


def _credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise TelegramConfigError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno"
        )
    return token, chat_id


def _fmt_usd(value: float) -> str:
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def _in_notify_range(price_usd: float, prefs: Prefs) -> bool:
    return prefs.min_price_usd <= price_usd <= prefs.max_price_usd


def _entered_notify_range(old_usd: float, new_usd: float, prefs: Prefs) -> bool:
    return not _in_notify_range(old_usd, prefs) and _in_notify_range(new_usd, prefs)


def _range_label(prefs: Prefs) -> str:
    return f"{prefs.min_price_usd}–{prefs.max_price_usd} USD"


def _qualify_reasons(listing: Listing, prefs: Prefs, price_usd: float | None) -> str:
    reasons: list[str] = []
    if listing.property_type == "house" and listing.locality:
        reasons.append(f"casa en {listing.locality}")
    elif listing.locality:
        reasons.append(listing.locality)
    if listing.is_gated:
        reasons.append("barrio cerrado")
    if listing.bedrooms is not None and listing.bedrooms >= prefs.min_bedrooms:
        reasons.append(f"{prefs.min_bedrooms}+ dormitorios")
    if listing.has_pool:
        reasons.append("pileta")
    if price_usd is not None and _in_notify_range(price_usd, prefs):
        reasons.append(_range_label(prefs))
    return ", ".join(reasons) if reasons else "cumple el brief de búsqueda"


def _listing_block(listing: Listing, price_usd: float | None) -> list[str]:
    barrio = listing.barrio_name.strip() if listing.barrio_name else None
    bedrooms = listing.bedrooms if listing.bedrooms is not None else None
    price_line = f"{_fmt_usd(price_usd)} USD" if price_usd is not None else "—"
    return [
        listing.title,
        f"Barrio: {barrio or '—'}",
        f"Dormitorios: {bedrooms if bedrooms is not None else '—'}",
        f"Pileta: {'sí' if listing.has_pool else 'no'}",
        f"Precio: {price_line}",
        listing.url,
    ]


def _format_new(listing: Listing, prefs: Prefs) -> str:
    lines = ["NUEVA", "", *_listing_block(listing, listing.price_usd), ""]
    lines.append(f"Por qué califica: {_qualify_reasons(listing, prefs, listing.price_usd)}")
    return "\n".join(lines)


def _format_price_drop(
    listing: Listing, old_usd: float, new_usd: float, prefs: Prefs
) -> str:
    drop = old_usd - new_usd
    lines = [
        "BAJÓ DE PRECIO",
        "",
        *_listing_block(listing, new_usd),
        f"antes {_fmt_usd(old_usd)} → ahora {_fmt_usd(new_usd)} (Δ -{_fmt_usd(drop)} USD)",
    ]
    if _entered_notify_range(old_usd, new_usd, prefs):
        lines.append(f"Entró al rango {_range_label(prefs)}")
    lines.append("")
    lines.append(f"Por qué califica: {_qualify_reasons(listing, prefs, new_usd)}")
    return "\n".join(lines)


def _send(text: str) -> None:
    token, chat_id = _credentials()
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    response = httpx.post(
        url,
        json={"chat_id": chat_id, "text": text},
        timeout=SEND_TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("description", "Telegram sendMessage failed"))
