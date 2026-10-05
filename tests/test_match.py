from pathlib import Path

import pytest

from jobs.run_once import run
from src.db import connect
from src.match import is_drop, is_match, is_watch
from src.models import Listing
from src.prefs import load_prefs


def make_listing(**overrides) -> Listing:
    data = {
        "source": "fake",
        "external_id": "FAKE-1",
        "url": "https://example.com/FAKE-1",
        "title": "Casa en barrio cerrado",
        "locality": "Tigre",
        "barrio_name": "La Comarca",
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
        "raw_hash": "hash-fake-1",
    }
    data.update(overrides)
    return Listing.model_validate(data)


@pytest.fixture
def prefs():
    return load_prefs()


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "scout.db"


def _fake_fetch(listings: list[Listing]):
    def fetch(_prefs=None, **_kwargs):
        return list(listings)

    return fetch


def _run(listings: list[Listing], *, db_path: Path, prefs, dry_run: bool = False):
    return run(
        prefs=prefs,
        fetchers=[_fake_fetch(listings)],
        db_path=db_path,
        dry_run=dry_run,
    )


def _count(db_path: Path, table: str) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])
    finally:
        conn.close()


def test_not_watch_three_bedrooms(prefs) -> None:
    listing = make_listing(bedrooms=3)
    assert is_watch(listing, prefs) is False
    assert is_match(listing, prefs) is False


def test_not_watch_no_pool(prefs) -> None:
    listing = make_listing(has_pool=False)
    assert is_watch(listing, prefs) is False


def test_not_watch_apartment(prefs) -> None:
    listing = make_listing(property_type="depto")
    assert is_watch(listing, prefs) is False


def test_not_watch_unknown_barrio(prefs) -> None:
    listing = make_listing(locality="Tigre", barrio_name="Villa Pacheco", title="Casa en Villa Pacheco")
    assert is_watch(listing, prefs) is False


def test_watch_listed_barrios(prefs) -> None:
    for barrio in prefs.barrios:
        listing = make_listing(barrio_name=barrio, locality="Tigre", title=f"Casa en {barrio}")
        assert is_watch(listing, prefs) is True, barrio
    accented = make_listing(
        barrio_name="Santa Bárbara",
        locality="Tigre",
        title="Casa en Santa Bárbara",
    )
    assert is_watch(accented, prefs) is True


def test_not_watch_without_price_usd(prefs) -> None:
    listing = make_listing(currency="ARS", price=2_000_000, price_usd=None)
    assert is_watch(listing, prefs) is False
    assert is_match(listing, prefs) is False


def test_2500_is_match(prefs) -> None:
    listing = make_listing(price=2500, price_usd=2500)
    assert is_watch(listing, prefs) is True
    assert is_match(listing, prefs) is True


def test_2600_is_not_watch(prefs) -> None:
    listing = make_listing(price=2600, price_usd=2600)
    assert is_watch(listing, prefs) is False
    assert is_match(listing, prefs) is False


def test_2200_is_match(prefs) -> None:
    listing = make_listing(price=2200, price_usd=2200)
    assert is_watch(listing, prefs) is True
    assert is_match(listing, prefs) is True


def test_is_drop_threshold_and_range(prefs) -> None:
    assert is_drop(2200, 2000, prefs) is True
    assert is_drop(2200, 2180, prefs) is False
    assert is_drop(3200, 2400, prefs) is True
    assert is_drop(2000, 2300, prefs) is False
    assert is_drop(2800, 2600, prefs) is False
    assert is_drop(None, 2000, prefs) is False


def test_over_watch_is_not_upserted(db_path: Path, prefs, monkeypatch) -> None:
    sent: list[str] = []
    monkeypatch.setattr("jobs.run_once.send_new", lambda listing: sent.append("new"))
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append("drop"),
    )

    stats = _run([make_listing(price=2600, price_usd=2600)], db_path=db_path, prefs=prefs)

    assert stats.fetched == 1
    assert stats.upserted == 0
    assert stats.new_notified == 0
    assert stats.drop_notified == 0
    assert sent == []
    assert _count(db_path, "listings") == 0
    assert _count(db_path, "notifications") == 0


def test_2200_first_new_then_silence_then_drop(
    db_path: Path, prefs, monkeypatch
) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(
        "jobs.run_once.send_new", lambda listing: sent.append(("new", listing.price_usd))
    )
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append(("drop", old_usd, new_usd)),
    )

    first = _run([make_listing(price=2200, price_usd=2200)], db_path=db_path, prefs=prefs)
    assert first.new_notified == 1
    assert first.drop_notified == 0
    assert sent == [("new", 2200)]

    sent.clear()
    second = _run([make_listing(price=2200, price_usd=2200)], db_path=db_path, prefs=prefs)
    assert second.new_notified == 0
    assert second.drop_notified == 0
    assert sent == []

    third = _run([make_listing(price=2000, price_usd=2000)], db_path=db_path, prefs=prefs)
    assert third.new_notified == 0
    assert third.drop_notified == 1
    assert sent == [("drop", 2200, 2000)]
    assert _count(db_path, "price_history") == 2
    assert _count(db_path, "notifications") == 2


def test_price_over_watch_is_not_tracked_for_later_drop(
    db_path: Path, prefs, monkeypatch
) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(
        "jobs.run_once.send_new", lambda listing: sent.append(("new", listing.price_usd))
    )
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append(("drop", old_usd, new_usd)),
    )

    first = _run([make_listing(price=3200, price_usd=3200)], db_path=db_path, prefs=prefs)
    assert first.upserted == 0
    assert first.new_notified == 0
    assert sent == []

    second = _run([make_listing(price=2400, price_usd=2400)], db_path=db_path, prefs=prefs)
    assert second.new_notified == 1
    assert second.drop_notified == 0
    assert sent == [("new", 2400)]
    assert _count(db_path, "notifications") == 1


def test_drop_of_20_usd_is_silent(db_path: Path, prefs, monkeypatch) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(
        "jobs.run_once.send_new", lambda listing: sent.append(("new", listing.price_usd))
    )
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append(("drop", old_usd, new_usd)),
    )

    _run([make_listing(price=2200, price_usd=2200)], db_path=db_path, prefs=prefs)
    sent.clear()

    stats = _run([make_listing(price=2180, price_usd=2180)], db_path=db_path, prefs=prefs)
    assert stats.drop_notified == 0
    assert stats.new_notified == 0
    assert sent == []
    assert _count(db_path, "price_history") == 2
    assert _count(db_path, "notifications") == 1


def test_price_increase_saves_history_no_notify(
    db_path: Path, prefs, monkeypatch
) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(
        "jobs.run_once.send_new", lambda listing: sent.append(("new", listing.price_usd))
    )
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append(("drop", old_usd, new_usd)),
    )

    _run([make_listing(price=2000, price_usd=2000)], db_path=db_path, prefs=prefs)
    sent.clear()
    stats = _run([make_listing(price=2300, price_usd=2300)], db_path=db_path, prefs=prefs)

    assert stats.drop_notified == 0
    assert stats.new_notified == 0
    assert sent == []
    assert _count(db_path, "price_history") == 2


def test_non_watch_listings_are_not_upserted(db_path: Path, prefs, monkeypatch) -> None:
    monkeypatch.setattr("jobs.run_once.send_new", lambda listing: None)
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop", lambda listing, old_usd, new_usd: None
    )
    listings = [
        make_listing(external_id="a", bedrooms=3, raw_hash="a"),
        make_listing(external_id="b", has_pool=False, raw_hash="b"),
        make_listing(external_id="c", property_type="depto", raw_hash="c"),
        make_listing(external_id="d", barrio_name="Tigre centro", locality="Tigre", title="Tigre centro", raw_hash="d"),
    ]
    stats = _run(listings, db_path=db_path, prefs=prefs)
    assert stats.fetched == 4
    assert stats.upserted == 0
    assert _count(db_path, "listings") == 0


def test_run_once_dry_run_mock_fetch_does_not_crash(db_path: Path, prefs) -> None:
    listing = make_listing()
    stats = _run([listing], db_path=db_path, prefs=prefs, dry_run=True)
    assert stats.fetched == 1
    assert stats.upserted == 1
    assert stats.new_notified == 1
    assert stats.drop_notified == 0
    assert _count(db_path, "notifications") == 0


def test_run_continues_if_one_adapter_fails(db_path: Path, prefs) -> None:
    def boom(_prefs=None, **_kwargs):
        raise RuntimeError("portal down")

    listing = make_listing(external_id="ok", raw_hash="ok")
    stats = run(
        prefs=prefs,
        fetchers=[boom, _fake_fetch([listing])],
        db_path=db_path,
        dry_run=True,
    )
    assert stats.fetched == 1
    assert stats.upserted == 1


def test_meli_html_failure_hints_waf(db_path: Path, prefs, caplog) -> None:
    def boom(_prefs=None, **_kwargs):
        raise RuntimeError("HTTP 403")

    boom.__module__ = "src.adapters.meli"
    listing = make_listing(external_id="ok", raw_hash="ok")
    with caplog.at_level("WARNING"):
        stats = run(
            prefs=prefs,
            fetchers=[boom, _fake_fetch([listing])],
            db_path=db_path,
            dry_run=True,
        )
    assert stats.fetched == 1
    assert any("_Desde_" in rec.message or "WAF" in rec.message for rec in caplog.records)


def test_enabled_adapters_is_zonaprop_only(prefs) -> None:
    from jobs.run_once import _available_fetchers

    assert prefs.enabled_adapters == ["zonaprop"]
    fetchers = _available_fetchers(prefs)
    modules = [fetch.__module__ for fetch in fetchers]
    assert modules == ["src.adapters.zonaprop"]
    assert all(fetch.__name__ == "fetch_listings" for fetch in fetchers)


def test_dry_run_then_live_sends_new_for_unnotified_match(
    db_path: Path, prefs, monkeypatch
) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(
        "jobs.run_once.send_new", lambda listing: sent.append(("new", listing.price_usd))
    )
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append(("drop", old_usd, new_usd)),
    )

    dry = _run(
        [make_listing(price=2200, price_usd=2200)],
        db_path=db_path,
        prefs=prefs,
        dry_run=True,
    )
    assert dry.new_notified == 1
    assert _count(db_path, "notifications") == 0
    assert sent == []

    live = _run(
        [make_listing(price=2200, price_usd=2200)],
        db_path=db_path,
        prefs=prefs,
        dry_run=False,
    )
    assert live.new_notified == 1
    assert live.drop_notified == 0
    assert sent == [("new", 2200)]
    assert _count(db_path, "notifications") == 1


def test_main_without_telegram_exits(monkeypatch) -> None:
    monkeypatch.setattr("jobs.run_once.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    from jobs.run_once import main

    with pytest.raises(SystemExit, match="TELEGRAM"):
        main([])
