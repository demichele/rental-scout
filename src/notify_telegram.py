from __future__ import annotations

import os
from html import escape

import httpx

from src.models import Listing
from src.prefs import Prefs, load_prefs

TELEGRAM_API = "https://api.telegram.org"
SEND_TIMEOUT = 20.0


class TelegramConfigError(RuntimeError):
    """Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID."""


def send_new(listing: Listing) -> None:
    prefs = load_prefs()
    _send(_format_new(listing, prefs), preview_url=listing.url)


def send_price_drop(listing: Listing, old_usd: float, new_usd: float) -> None:
    prefs = load_prefs()
    _send(_format_price_drop(listing, old_usd, new_usd, prefs), preview_url=listing.url)


def _credentials() -> tuple[str, str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        raise TelegramConfigError(
            "Faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno"
        )
    bot_id = token.split(":", 1)[0]
    if chat_id == bot_id:
        raise TelegramConfigError(
            "TELEGRAM_CHAT_ID es el id del bot, no el tuyo. "
            "Abrí el chat con el bot, mandá /start, y poné el chat.id "
            "de TU usuario (getUpdates o @userinfobot). "
            "El id del bot es la parte numérica del token; no lo uses como chat."
        )
    return token, chat_id


def require_credentials() -> None:
    """Falla si Telegram no está configurado. Para el job real, no el dry-run."""
    _credentials()


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
    place = listing.barrio_name or listing.locality
    if listing.property_type == "house" and place:
        reasons.append(f"casa en {place}")
    elif place:
        reasons.append(place)
    if listing.is_gated:
        reasons.append("barrio cerrado")
    if listing.ambientes is not None and listing.ambientes >= prefs.min_ambientes:
        reasons.append(f"{prefs.min_ambientes}+ ambientes")
    if listing.bedrooms is not None and listing.bedrooms >= prefs.min_bedrooms:
        reasons.append(f"{prefs.min_bedrooms}+ dormitorios")
    if listing.has_pool:
        reasons.append("pileta")
    if price_usd is not None and _in_notify_range(price_usd, prefs):
        reasons.append(_range_label(prefs))
    return ", ".join(reasons) if reasons else "cumple el brief de búsqueda"


def _html(text: str) -> str:
    return escape(text, quote=False)


def _anuncio_link(listing: Listing) -> str:
    url = (listing.url or "").strip()
    if url.startswith("http://") or url.startswith("https://"):
        return f'<a href="{escape(url, quote=True)}">Ver anuncio</a>'
    if url:
        return f"Ver anuncio: {_html(url)}"
    return "Ver anuncio: —"


def _listing_block(listing: Listing, price_usd: float | None) -> list[str]:
    barrio = listing.barrio_name.strip() if listing.barrio_name else None
    bedrooms = listing.bedrooms if listing.bedrooms is not None else None
    price_line = f"{_fmt_usd(price_usd)} USD" if price_usd is not None else "—"
    return [
        _html(listing.title),
        f"Barrio: {_html(barrio or '—')}",
        f"Dormitorios: {bedrooms if bedrooms is not None else '—'}",
        f"Pileta: {'sí' if listing.has_pool else 'no'}",
        f"Precio: {price_line}",
        _anuncio_link(listing),
    ]


def _place(listing: Listing) -> str:
    barrio = (listing.barrio_name or "").strip()
    if barrio:
        return barrio
    locality = (listing.locality or "").strip()
    return locality or "—"


def _price_label(price_usd: float | None) -> str:
    if price_usd is None:
        return "—"
    return f"{_fmt_usd(price_usd)} USD"


def _format_new(listing: Listing, prefs: Prefs) -> str:
    headline = f"Casa en {_html(_place(listing))} por {_price_label(listing.price_usd)}"
    lines = [headline, "", *_listing_block(listing, listing.price_usd), ""]
    lines.append(f"Por qué califica: {_html(_qualify_reasons(listing, prefs, listing.price_usd))}")
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
    lines.append(f"Por qué califica: {_html(_qualify_reasons(listing, prefs, new_usd))}")
    return "\n".join(lines)


def _send(text: str, *, preview_url: str | None = None) -> None:
    token, chat_id = _credentials()
    url = f"{TELEGRAM_API}/bot{token}/sendMessage"
    payload: dict[str, object] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
    }
    preview = (preview_url or "").strip()
    if preview.startswith("http://") or preview.startswith("https://"):
        payload["link_preview_options"] = {"is_disabled": False, "url": preview}
    response = httpx.post(
        url,
        json=payload,
        timeout=SEND_TIMEOUT,
    )
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    description = payload.get("description") or response.text[:300]
    if response.is_error or not payload.get("ok", True):
        raise RuntimeError(
            f"Telegram sendMessage {response.status_code}: {description} "
            f"(chat_id={chat_id!r}). "
            "El bot tiene que haber recibido un /start tuyo; "
            "si el token salió en un log, revocalo en BotFather."
        )
