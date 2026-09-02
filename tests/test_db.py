from pathlib import Path

import pytest

from src.db import (
    already_notified_drop,
    already_notified_new,
    connect,
    record_notification,
    upsert_listing,
)
from src.models import Listing


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


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "scout.db"


def _count(db_path: Path, table: str) -> int:
    conn = connect(db_path)
    try:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        return int(row["n"])
    finally:
        conn.close()


def test_same_listing_same_price_writes_one_history(db_path: Path) -> None:
    listing = make_listing()
    first = upsert_listing(listing, db_path=db_path)
    second = upsert_listing(listing, db_path=db_path)

    assert first.created is True
    assert first.price_changed is False
    assert second.created is False
    assert second.price_changed is False
    assert _count(db_path, "listings") == 1
    assert _count(db_path, "price_history") == 1


def test_lower_price_appends_history_and_flags_change(db_path: Path) -> None:
    upsert_listing(make_listing(price=2200, price_usd=2200), db_path=db_path)
    result = upsert_listing(make_listing(price=2000, price_usd=2000), db_path=db_path)

    assert result.created is False
    assert result.price_changed is True
    assert result.old_price_usd == 2200
    assert result.new_price_usd == 2000
    assert _count(db_path, "listings") == 1
    assert _count(db_path, "price_history") == 2


def test_price_increase_saves_history_but_is_not_a_drop(db_path: Path) -> None:
    listing = make_listing(price=2000, price_usd=2000)
    upsert_listing(listing, db_path=db_path)
    result = upsert_listing(make_listing(price=2300, price_usd=2300), db_path=db_path)

    assert result.price_changed is True
    assert result.old_price_usd == 2000
    assert result.new_price_usd == 2300
    assert _count(db_path, "price_history") == 2
    assert already_notified_drop(listing.source, listing.external_id, 2300, db_path=db_path) is False
    assert _count(db_path, "notifications") == 0


def test_different_external_id_inserts_another_listing(db_path: Path) -> None:
    upsert_listing(make_listing(external_id="MLA123", raw_hash="h1"), db_path=db_path)
    result = upsert_listing(
        make_listing(external_id="MLA999", url="https://example.com/MLA999", raw_hash="h2"),
        db_path=db_path,
    )

    assert result.created is True
    assert _count(db_path, "listings") == 2
    assert _count(db_path, "price_history") == 2


def test_notification_helpers_do_not_repeat(db_path: Path) -> None:
    listing = make_listing()
    upsert_listing(listing, db_path=db_path)

    assert already_notified_new(listing.source, listing.external_id, db_path=db_path) is False
    record_notification("new", listing.source, listing.external_id, new_usd=2200, db_path=db_path)
    assert already_notified_new(listing.source, listing.external_id, db_path=db_path) is True

    record_notification(
        "price_drop",
        listing.source,
        listing.external_id,
        old_usd=2200,
        new_usd=2000,
        db_path=db_path,
    )
    assert already_notified_drop(listing.source, listing.external_id, 2000, db_path=db_path) is True
    assert already_notified_drop(listing.source, listing.external_id, 1900, db_path=db_path) is False
