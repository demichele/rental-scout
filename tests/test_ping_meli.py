from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from src.adapters.meli import (
    SEARCH_URL,
    USER_AGENT,
    USERS_ME_URL,
    check_connection,
)


def _json_response(status: int, payload: dict) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.headers = {"content-type": "application/json"}
    response.content = b"{}"
    response.json.return_value = payload
    if status >= 400:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error",
            request=MagicMock(),
            response=response,
        )
    else:
        response.raise_for_status.return_value = None
    return response


def test_check_connection_missing_token(monkeypatch) -> None:
    monkeypatch.delenv("MELI_ACCESS_TOKEN", raising=False)
    result = check_connection(client=MagicMock())
    assert result.ok is False
    assert "MELI_ACCESS_TOKEN" in result.message


def test_check_connection_rejects_client_secret(monkeypatch) -> None:
    monkeypatch.setenv("MELI_ACCESS_TOKEN", "a" * 32)
    client = MagicMock()
    result = check_connection(client=client)
    assert result.ok is False
    assert "APP_USR-" in result.message
    client.get.assert_not_called()


def test_check_connection_token_rejected_on_users_me(monkeypatch) -> None:
    monkeypatch.setenv("MELI_ACCESS_TOKEN", "APP_USR-123-expired-token-placeholder-xxxx")
    client = MagicMock()
    client.get.return_value = _json_response(403, {"message": "invalid"})

    result = check_connection(client=client)

    assert result.ok is False
    assert "rechazó el access token" in result.message
    assert client.get.call_args.args[0] == USERS_ME_URL
    assert client.get.call_count == 1


def test_check_connection_ok_users_me_and_search(monkeypatch) -> None:
    monkeypatch.setenv("MELI_ACCESS_TOKEN", "APP_USR-123-valid-token-placeholder-xxxx")
    me = _json_response(200, {"id": 111, "nickname": "TESTUSER"})
    search = _json_response(200, {"paging": {"total": 42}, "results": [{"id": "MLA1"}]})

    def fake_get(url, **kwargs):
        if str(url) == USERS_ME_URL:
            return me
        return search

    client = MagicMock()
    client.get.side_effect = fake_get

    result = check_connection(client=client)

    assert result.ok is True
    assert result.user_id == "111"
    assert result.nickname == "TESTUSER"
    assert result.search_http == 200
    assert result.search_total == 42
    urls = [str(call.args[0]) for call in client.get.call_args_list]
    assert urls == [USERS_ME_URL, SEARCH_URL]
    search_kwargs = client.get.call_args_list[1].kwargs
    assert search_kwargs["params"]["limit"] == 1
    assert search_kwargs["headers"]["User-Agent"] == USER_AGENT
    assert search_kwargs["headers"]["Authorization"].startswith("Bearer APP_USR-")


def test_check_connection_search_forbidden_after_valid_me(monkeypatch) -> None:
    monkeypatch.setenv("MELI_ACCESS_TOKEN", "APP_USR-123-valid-token-placeholder-xxxx")
    me = _json_response(200, {"id": 111, "nickname": "TESTUSER"})
    search = _json_response(403, {"message": "forbidden"})

    def fake_get(url, **kwargs):
        if str(url) == USERS_ME_URL:
            return me
        return search

    client = MagicMock()
    client.get.side_effect = fake_get

    result = check_connection(client=client)

    assert result.ok is False
    assert result.user_id == "111"
    assert result.search_http == 403
    assert "búsqueda devolvió HTTP 403" in result.message


def test_ping_meli_job_prints_ok(monkeypatch, capsys) -> None:
    from src.adapters.meli import ConnectionCheck

    monkeypatch.setattr("jobs.ping_meli.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "jobs.ping_meli.check_connection",
        lambda: ConnectionCheck(
            ok=True,
            message="ok: Mercado Libre aceptó el token y la búsqueda",
            user_id="111",
            nickname="TESTUSER",
            search_http=200,
            search_total=42,
        ),
    )
    from jobs.ping_meli import main

    main()
    out = capsys.readouterr().out
    assert "ok: Mercado Libre aceptó el token" in out
    assert "user_id=111" in out
    assert "search HTTP 200 total=42" in out


def test_ping_meli_job_exits_on_failure(monkeypatch) -> None:
    from src.adapters.meli import ConnectionCheck

    monkeypatch.setattr("jobs.ping_meli.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "jobs.ping_meli.check_connection",
        lambda: ConnectionCheck(ok=False, message="falta MELI_ACCESS_TOKEN en .env"),
    )
    from jobs.ping_meli import main

    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
