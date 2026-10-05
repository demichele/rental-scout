from pathlib import Path

import pytest

from jobs.run_once import run
from src.db import connect
from src.match import is_drop, is_match, is_watch
from src.models import Listing
from src.prefs import load_prefs, load_sale_prefs


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
def sale_prefs():
    prefs = load_sale_prefs()
    assert prefs is not None
    return prefs


def test_sale_watch_ignores_rental_size_and_barrios(sale_prefs, prefs) -> None:
    listing = make_listing(
        operation="sale",
        source="zonaprop_venta",
        locality="Tigre",
        barrio_name="Tigre centro",
        title="Casa en Tigre centro",
        bedrooms=2,
        ambientes=None,
        price=55000,
        price_usd=55000,
        is_gated=False,
        has_pool=False,
    )
    assert is_watch(listing, sale_prefs) is True
    assert is_match(listing, sale_prefs) is True
    assert is_watch(listing, prefs) is False


def test_sale_watch_pacheco_at_cap(sale_prefs) -> None:
    listing = make_listing(
        operation="sale",
        source="zonaprop_venta",
        locality="General Pacheco",
        barrio_name=None,
        title="Casa en General Pacheco",
        bedrooms=None,
        ambientes=None,
        price=80000,
        price_usd=80000,
    )
    assert is_watch(listing, sale_prefs) is True
    assert is_match(listing, sale_prefs) is True


def test_sale_drops_over_80k_and_other_partido(sale_prefs) -> None:
    over = make_listing(
        operation="sale",
        locality="Tigre",
        barrio_name="Tigre",
        title="Casa en Tigre",
        bedrooms=None,
        ambientes=None,
        price=80001,
        price_usd=80001,
    )
    other = make_listing(
        operation="sale",
        locality="Escobar",
        barrio_name="Escobar",
        title="Casa en Escobar",
        bedrooms=None,
        ambientes=None,
        price=50000,
        price_usd=50000,
    )
    assert is_watch(over, sale_prefs) is False
    assert is_watch(other, sale_prefs) is False


def test_sale_drops_zero_and_below_10k(sale_prefs) -> None:
    zero = make_listing(
        operation="sale",
        locality="Tigre",
        barrio_name="Tigre",
        title="Casa en Tigre",
        bedrooms=None,
        ambientes=None,
        price=0,
        price_usd=0,
    )
    cheap = make_listing(
        operation="sale",
        locality="Tigre",
        barrio_name="Tigre",
        title="Casa en Tigre",
        bedrooms=None,
        ambientes=None,
        price=9999,
        price_usd=9999,
    )
    floor = make_listing(
        operation="sale",
        locality="Tigre",
        barrio_name="Tigre",
        title="Casa en Tigre",
        bedrooms=None,
        ambientes=None,
        price=10000,
        price_usd=10000,
    )
    assert is_watch(zero, sale_prefs) is False
    assert is_match(zero, sale_prefs) is False
    assert is_watch(cheap, sale_prefs) is False
    assert is_match(cheap, sale_prefs) is False
    assert is_watch(floor, sale_prefs) is True
    assert is_match(floor, sale_prefs) is True


def test_sale_and_rent_do_not_share_notification_identity(
    db_path: Path, prefs, sale_prefs, monkeypatch
) -> None:
    sent: list[tuple] = []
    monkeypatch.setattr(
        "jobs.run_once.send_new",
        lambda listing: sent.append((listing.source, listing.operation, listing.price_usd)),
    )
    monkeypatch.setattr(
        "jobs.run_once.send_price_drop",
        lambda listing, old_usd, new_usd: sent.append(("drop", listing.source)),
    )
    rent = make_listing(source="zonaprop", external_id="SAME-ID", raw_hash="rent")
    sale = make_listing(
        operation="sale",
        source="zonaprop_venta",
        external_id="SAME-ID",
        locality="Tigre",
        barrio_name="Tigre",
        title="Casa en Tigre",
        bedrooms=2,
        ambientes=None,
        price=55000,
        price_usd=55000,
        raw_hash="sale",
    )
    rent_stats = _run([rent], db_path=db_path, prefs=prefs)
    sale_stats = _run([sale], db_path=db_path, prefs=sale_prefs)
    assert rent_stats.new_notified == 1
    assert sale_stats.new_notified == 1
    assert sent == [
        ("zonaprop", "rent", 2200),
        ("zonaprop_venta", "sale", 55000),
    ]
    assert _count(db_path, "listings") == 2
    assert _count(db_path, "notifications") == 2


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


def test_watch_three_bedrooms_if_five_ambientes(prefs) -> None:
    listing = make_listing(bedrooms=3, ambientes=5)
    assert is_watch(listing, prefs) is True
    assert is_match(listing, prefs) is True


def test_not_watch_two_bedrooms(prefs) -> None:
    listing = make_listing(bedrooms=2, ambientes=5)
    assert is_watch(listing, prefs) is False


def test_not_watch_four_ambientes(prefs) -> None:
    listing = make_listing(bedrooms=4, ambientes=4)
    assert is_watch(listing, prefs) is False


def test_watch_without_pool_or_gated_evidence(prefs) -> None:
    listing = make_listing(has_pool=False, is_gated=False)
    assert is_watch(listing, prefs) is True


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
    for talar in ("Talar del Lago I", "Talar del Lago II"):
        listing = make_listing(barrio_name=talar, locality="Tigre", title=f"Casa en {talar}")
        assert is_watch(listing, prefs) is True, talar


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
        make_listing(external_id="a", bedrooms=2, ambientes=5, raw_hash="a"),
        make_listing(external_id="b", ambientes=4, raw_hash="b"),
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
    monkeypatch.delenv("TELEGRAM_CHAT_IDS", raising=False)
    from jobs.run_once import main

    with pytest.raises(SystemExit, match="TELEGRAM"):
        main([])
