from unittest.mock import MagicMock

import pytest

from src.models import Listing
from src.notify_telegram import TelegramConfigError, send_new, send_price_drop
from src.prefs import load_prefs


def make_listing(**overrides) -> Listing:
    data = {
        "source": "meli",
        "external_id": "MLA123",
        "url": "https://example.com/MLA123",
        "title": "Casa en barrio cerrado",
        "locality": "General Pacheco",
        "barrio_name": "San Pablo",
        "is_gated": True,
        "bedrooms": 4,
        "ambientes": 5,
        "bathrooms": 3,
        "m2": 220.0,
        "has_pool": True,
        "currency": "USD",
        "price": 2200,
        "price_usd": 2200,
        "expenses": None,
        "property_type": "house",
        "published_at": None,
        "photos": ["https://example.com/1.jpg"],
        "lat": None,
        "lng": None,
        "raw_hash": "hash-mla123",
    }
    data.update(overrides)
    return Listing.model_validate(data)


def _ok_response() -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.is_error = False
    response.text = ""
    response.raise_for_status.return_value = None
    response.json.return_value = {"ok": True, "result": {"message_id": 1}}
    return response


@pytest.fixture
def telegram_http(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "12345")
    mock_post = MagicMock(return_value=_ok_response())
    monkeypatch.setattr("src.notify_telegram.httpx.post", mock_post)
    return mock_post


def _payload(mock_post) -> dict:
    return mock_post.call_args.kwargs["json"]


def _text(mock_post) -> str:
    return _payload(mock_post)["text"]


def test_send_new_posts_listing_fields(telegram_http) -> None:
    send_new(make_listing())

    telegram_http.assert_called_once()
    url = telegram_http.call_args.args[0]
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    payload = _payload(telegram_http)
    assert payload["chat_id"] == "12345"
    text = payload["text"]
    assert text.startswith("NUEVA")
    assert "Casa en barrio cerrado" in text
    assert "Barrio: San Pablo" in text
    assert "Dormitorios: 4" in text
    assert "Pileta: sí" in text
    assert "Precio: 2200 USD" in text
    assert "Ver anuncio" in text
    assert "https://example.com/MLA123" in text
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"]["url"] == "https://example.com/MLA123"
    assert "Por qué califica:" in text
    assert "barrio cerrado" in text
    assert "3+ dormitorios" in text
    assert "5+ ambientes" in text
    assert "pileta" in text
    assert "1500–2500 USD" in text


def test_send_price_drop_includes_delta(telegram_http) -> None:
    send_price_drop(make_listing(price=2000, price_usd=2000), old_usd=2300, new_usd=2000)

    text = _text(telegram_http)
    assert text.startswith("BAJÓ DE PRECIO")
    assert "Casa en barrio cerrado" in text
    assert "Barrio: San Pablo" in text
    assert "Dormitorios: 4" in text
    assert "Pileta: sí" in text
    assert "Precio: 2000 USD" in text
    assert "Ver anuncio" in text
    assert "https://example.com/MLA123" in text
    assert "antes 2300 → ahora 2000 (Δ -300 USD)" in text
    assert "Por qué califica:" in text
    assert "Entró al rango" not in text


def test_send_price_drop_entered_range(telegram_http) -> None:
    prefs = load_prefs()
    send_price_drop(make_listing(price=2200, price_usd=2200), old_usd=3000, new_usd=2200)

    text = _text(telegram_http)
    assert "antes 3000 → ahora 2200 (Δ -800 USD)" in text
    assert f"Entró al rango {prefs.min_price_usd}–{prefs.max_price_usd} USD" in text
    assert "Entró al rango 1500–2500 USD" in text


def test_missing_env_does_not_call_httpx(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    mock_post = MagicMock()
    monkeypatch.setattr("src.notify_telegram.httpx.post", mock_post)

    with pytest.raises(TelegramConfigError, match="TELEGRAM_BOT_TOKEN"):
        send_new(make_listing())

    mock_post.assert_not_called()


def test_require_credentials_uses_same_env(telegram_http) -> None:
    from src.notify_telegram import require_credentials

    require_credentials()
    telegram_http.assert_not_called()


def test_telegram_ok_false_raises(telegram_http) -> None:
    telegram_http.return_value.json.return_value = {
        "ok": False,
        "description": "Bad Request: chat not found",
    }

    with pytest.raises(RuntimeError, match="chat not found"):
        send_new(make_listing())
