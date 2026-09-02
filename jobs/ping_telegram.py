from pathlib import Path

from dotenv import load_dotenv

from src.models import Listing
from src.notify_telegram import TelegramConfigError, send_new, send_price_drop

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _fake_listing(**overrides) -> Listing:
    data = {
        "source": "ping",
        "external_id": "PING-NEW",
        "url": "https://www.zonaprop.com.ar/propiedades/clasificado/alclapin-ping-nueva.html",
        "title": "Casa 4 dorm con pileta en San Pablo",
        "locality": "General Pacheco",
        "barrio_name": "San Pablo",
        "is_gated": True,
        "bedrooms": 4,
        "bathrooms": 3,
        "m2": 220.0,
        "has_pool": True,
        "currency": "USD",
        "price": 2200,
        "price_usd": 2200,
        "expenses": None,
        "property_type": "house",
        "published_at": None,
        "photos": [],
        "lat": None,
        "lng": None,
        "raw_hash": "ping-nueva",
    }
    data.update(overrides)
    return Listing.model_validate(data)


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    try:
        send_new(
            _fake_listing(
                title="[ping] Casa 4 dorm con pileta en San Pablo",
            )
        )
        send_price_drop(
            _fake_listing(
                external_id="PING-DROP",
                url="https://www.zonaprop.com.ar/propiedades/clasificado/alclapin-ping-bajo.html",
                title="[ping] Casa 4 dorm — bajó al rango",
                price=2200,
                price_usd=2200,
                raw_hash="ping-drop",
            ),
            old_usd=3000,
            new_usd=2200,
        )
    except TelegramConfigError as exc:
        raise SystemExit(
            f"{exc}. Copiá .env.example a .env y completá las credenciales."
        ) from exc
    print("ok: enviados NUEVA y BAJÓ")


if __name__ == "__main__":
    main()
