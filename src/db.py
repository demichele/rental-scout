from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.models import Listing, NotificationKind

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "scout.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    locality TEXT NOT NULL,
    barrio_name TEXT,
    is_gated INTEGER NOT NULL,
    bedrooms INTEGER,
    bathrooms REAL,
    m2 REAL,
    has_pool INTEGER NOT NULL,
    currency TEXT NOT NULL,
    price REAL NOT NULL,
    price_usd REAL,
    expenses REAL,
    property_type TEXT NOT NULL,
    published_at TEXT,
    photos TEXT NOT NULL DEFAULT '[]',
    lat REAL,
    lng REAL,
    raw_hash TEXT NOT NULL,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    last_price_usd REAL,
    UNIQUE(source, external_id)
);

CREATE TABLE IF NOT EXISTS price_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_pk INTEGER NOT NULL REFERENCES listings(id),
    observed_at TEXT NOT NULL,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    price_usd REAL
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK (kind IN ('new', 'price_drop')),
    listing_pk INTEGER NOT NULL REFERENCES listings(id),
    old_usd REAL,
    new_usd REAL,
    sent_at TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class UpsertResult:
    created: bool
    price_changed: bool
    old_price_usd: float | None
    new_price_usd: float | None


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prices_equal(left: float | None, right: float | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return float(left) == float(right)


def _published_at_iso(listing: Listing) -> str | None:
    if listing.published_at is None:
        return None
    return listing.published_at.isoformat()


def _listing_values(listing: Listing, *, seen_at: str) -> tuple:
    return (
        listing.source,
        listing.external_id,
        listing.url,
        listing.title,
        listing.locality,
        listing.barrio_name,
        int(listing.is_gated),
        listing.bedrooms,
        listing.bathrooms,
        listing.m2,
        int(listing.has_pool),
        listing.currency,
        listing.price,
        listing.price_usd,
        listing.expenses,
        listing.property_type,
        _published_at_iso(listing),
        json.dumps(listing.photos),
        listing.lat,
        listing.lng,
        listing.raw_hash,
        seen_at,
        listing.price_usd,
    )


def _get_listing_row(
    conn: sqlite3.Connection, source: str, external_id: str
) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM listings WHERE source = ? AND external_id = ?",
        (source, external_id),
    ).fetchone()


def _append_price_history(
    conn: sqlite3.Connection, listing_pk: int, listing: Listing, observed_at: str
) -> None:
    conn.execute(
        """
        INSERT INTO price_history (listing_pk, observed_at, price, currency, price_usd)
        VALUES (?, ?, ?, ?, ?)
        """,
        (listing_pk, observed_at, listing.price, listing.currency, listing.price_usd),
    )


def upsert_listing(listing: Listing, *, db_path: Path | None = None) -> UpsertResult:
    now = _now_iso()
    conn = connect(db_path)
    try:
        row = _get_listing_row(conn, listing.source, listing.external_id)
        if row is None:
            cur = conn.execute(
                """
                INSERT INTO listings (
                    source, external_id, url, title, locality, barrio_name,
                    is_gated, bedrooms, bathrooms, m2, has_pool, currency, price,
                    price_usd, expenses, property_type, published_at, photos,
                    lat, lng, raw_hash, first_seen, last_seen, last_price_usd
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (*_listing_values(listing, seen_at=now)[:-1], now, listing.price_usd),
            )
            listing_pk = int(cur.lastrowid)
            _append_price_history(conn, listing_pk, listing, now)
            conn.commit()
            return UpsertResult(
                created=True,
                price_changed=False,
                old_price_usd=None,
                new_price_usd=listing.price_usd,
            )

        listing_pk = int(row["id"])
        old_price = row["last_price_usd"]
        price_changed = not _prices_equal(old_price, listing.price_usd)
        conn.execute(
            """
            UPDATE listings SET
                url = ?, title = ?, locality = ?, barrio_name = ?,
                is_gated = ?, bedrooms = ?, bathrooms = ?, m2 = ?, has_pool = ?,
                currency = ?, price = ?, price_usd = ?, expenses = ?,
                property_type = ?, published_at = ?, photos = ?, lat = ?, lng = ?,
                raw_hash = ?, last_seen = ?, last_price_usd = ?
            WHERE id = ?
            """,
            (
                listing.url,
                listing.title,
                listing.locality,
                listing.barrio_name,
                int(listing.is_gated),
                listing.bedrooms,
                listing.bathrooms,
                listing.m2,
                int(listing.has_pool),
                listing.currency,
                listing.price,
                listing.price_usd,
                listing.expenses,
                listing.property_type,
                _published_at_iso(listing),
                json.dumps(listing.photos),
                listing.lat,
                listing.lng,
                listing.raw_hash,
                now,
                listing.price_usd,
                listing_pk,
            ),
        )
        if price_changed:
            _append_price_history(conn, listing_pk, listing, now)
        conn.commit()
        return UpsertResult(
            created=False,
            price_changed=price_changed,
            old_price_usd=float(old_price) if old_price is not None else None,
            new_price_usd=listing.price_usd,
        )
    finally:
        conn.close()


def already_notified_new(
    source: str, external_id: str, *, db_path: Path | None = None
) -> bool:
    conn = connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM notifications n
            JOIN listings l ON l.id = n.listing_pk
            WHERE l.source = ? AND l.external_id = ? AND n.kind = 'new'
            LIMIT 1
            """,
            (source, external_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def already_notified_drop(
    source: str,
    external_id: str,
    new_usd: float,
    *,
    db_path: Path | None = None,
) -> bool:
    conn = connect(db_path)
    try:
        row = conn.execute(
            """
            SELECT 1
            FROM notifications n
            JOIN listings l ON l.id = n.listing_pk
            WHERE l.source = ? AND l.external_id = ?
              AND n.kind = 'price_drop' AND n.new_usd = ?
            LIMIT 1
            """,
            (source, external_id, new_usd),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def record_notification(
    kind: NotificationKind,
    source: str,
    external_id: str,
    *,
    old_usd: float | None = None,
    new_usd: float | None = None,
    db_path: Path | None = None,
) -> None:
    conn = connect(db_path)
    try:
        listing_row = _get_listing_row(conn, source, external_id)
        if listing_row is None:
            raise ValueError(f"listing not found: {source}/{external_id}")
        conn.execute(
            """
            INSERT INTO notifications (kind, listing_pk, old_usd, new_usd, sent_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (kind, int(listing_row["id"]), old_usd, new_usd, _now_iso()),
        )
        conn.commit()
    finally:
        conn.close()
