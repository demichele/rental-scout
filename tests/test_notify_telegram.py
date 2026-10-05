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
        "expenses": 150000,
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


def test_send_new_sale_uses_venta_headline(telegram_http) -> None:
    send_new(
        make_listing(
            operation="sale",
            source="zonaprop_venta",
            locality="Tigre",
            barrio_name="Tigre centro",
            title="Casa en venta en Tigre",
            price=55000,
            price_usd=55000,
            bedrooms=2,
        )
    )
    text = _text(telegram_http)
    assert text.startswith("VENTA - CASA EN TIGRE por 55.000 USD")
    assert "Casa en Tigre centro por" not in text
    assert "Por qué califica:" not in text
    assert "1500–2500" not in text
    assert "Casa en venta en Tigre" in text
    assert "Precio: 55000 USD" in text


def test_send_new_sale_pacheco_headline(telegram_http) -> None:
    send_new(
        make_listing(
            operation="sale",
            source="zonaprop_venta",
            locality="General Pacheco",
            barrio_name=None,
            title="Casa en General Pacheco",
            price=80000,
            price_usd=80000,
        )
    )
    text = _text(telegram_http)
    assert text.startswith("VENTA - CASA EN PACHECO por 80.000 USD")


def test_send_price_drop_sale_keeps_venta_line(telegram_http) -> None:
    send_price_drop(
        make_listing(
            operation="sale",
            source="zonaprop_venta",
            locality="Tigre",
            barrio_name="Tigre",
            title="Casa en venta",
            price=50000,
            price_usd=50000,
        ),
        old_usd=55000,
        new_usd=50000,
    )
    text = _text(telegram_http)
    assert text.startswith("BAJÓ DE PRECIO")
    assert "VENTA - CASA EN TIGRE por 50.000 USD" in text
    assert "Por qué califica:" not in text
    assert "antes 55000 → ahora 50000" in text


def test_send_new_posts_listing_fields(telegram_http) -> None:
    send_new(make_listing())

    telegram_http.assert_called_once()
    url = telegram_http.call_args.args[0]
    assert url == "https://api.telegram.org/bottest-token/sendMessage"
    payload = _payload(telegram_http)
    assert payload["chat_id"] == "12345"
    text = payload["text"]
    assert text.startswith("Casa en San Pablo por 2200 USD")
    assert not text.startswith("NUEVA")
    assert "Casa en barrio cerrado" in text
    assert "Barrio: San Pablo" in text
    assert "Dormitorios: 4" in text
    assert "Pileta:" not in text
    assert "pileta" not in text.casefold()
    assert "Precio: 2200 USD" in text
    assert "Expensas: $ 150.000" in text
    assert "Ver anuncio" in text
    assert "https://example.com/MLA123" in text
    assert payload["parse_mode"] == "HTML"
    assert payload["link_preview_options"]["url"] == "https://example.com/MLA123"
    assert "Por qué califica:" in text
    assert "barrio cerrado" in text
    assert "3+ dormitorios" in text
    assert "5+ ambientes" in text
    assert "1500–2500 USD" in text


def test_send_price_drop_includes_delta(telegram_http) -> None:
    send_price_drop(make_listing(price=2000, price_usd=2000), old_usd=2300, new_usd=2000)

    text = _text(telegram_http)
    assert text.startswith("BAJÓ DE PRECIO")
    assert "Casa en barrio cerrado" in text
    assert "Barrio: San Pablo" in text
    assert "Dormitorios: 4" in text
    assert "Pileta:" not in text
    assert "Precio: 2000 USD" in text
    assert "Expensas: $ 150.000" in text
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


def test_send_new_expenses_missing_shows_dash(telegram_http) -> None:
    send_new(make_listing(expenses=None))

    text = _text(telegram_http)
    assert "Expensas: —" in text
    assert "Pileta:" not in text


def test_send_new_fans_out_to_chat_ids(telegram_http, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "111, 222")
    send_new(make_listing())

    chats = [
        call.kwargs["json"]["chat_id"] for call in telegram_http.call_args_list
    ]
    assert chats == ["111", "222"]
    assert telegram_http.call_count == 2


def test_send_new_chat_ids_only(telegram_http, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "222,333")
    send_new(make_listing())

    chats = [
        call.kwargs["json"]["chat_id"] for call in telegram_http.call_args_list
    ]
    assert chats == ["222", "333"]


def test_send_new_continues_if_one_chat_fails(telegram_http, monkeypatch) -> None:
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "111")
    monkeypatch.setenv("TELEGRAM_CHAT_IDS", "222")
    bad = _ok_response()
    bad.is_error = True
    bad.status_code = 400
    bad.json.return_value = {"ok": False, "description": "chat not found"}
    telegram_http.side_effect = [bad, _ok_response()]

    send_new(make_listing())

    chats = [
        call.kwargs["json"]["chat_id"] for call in telegram_http.call_args_list
    ]
    assert chats == ["111", "222"]


def test_missing_env_does_not_call_httpx(monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_IDS", raising=False)
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
