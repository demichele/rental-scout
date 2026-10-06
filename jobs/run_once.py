from __future__ import annotations

import argparse
import importlib
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from src.db import (
    UpsertResult,
    already_notified_drop,
    already_notified_new,
    record_notification,
    upsert_listing,
)
from src.match import is_drop, is_match, is_watch
from src.models import Listing
from src.notify_telegram import TelegramConfigError, require_credentials, send_new, send_price_drop
from src.prefs import Prefs, load_prefs, load_searches

PROJECT_ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger(__name__)

Fetcher = Callable[..., list[Listing]]


@dataclass(frozen=True)
class RunStats:
    fetched: int
    upserted: int
    new_notified: int
    drop_notified: int


KNOWN_ADAPTERS = ("zonaprop", "meli", "argenprop")


def _available_fetchers(prefs: Prefs) -> list[Fetcher]:
    names = [name.strip() for name in prefs.enabled_adapters if name.strip()]
    if not names:
        log.warning("enabled_adapters está vacío; no se consulta ningún portal")
        return []
    fetchers: list[Fetcher] = []
    for name in names:
        if name not in KNOWN_ADAPTERS:
            log.warning("adapter desconocido %s (conocidos: %s)", name, ", ".join(KNOWN_ADAPTERS))
            continue
        try:
            module = importlib.import_module(f"src.adapters.{name}")
        except ImportError:
            log.warning("adapter %s no está instalado", name)
            continue
        fetch = getattr(module, "fetch_listings", None)
        if callable(fetch):
            fetchers.append(fetch)
        else:
            log.warning("adapter %s no expone fetch_listings", name)
    return fetchers


def run(
    *,
    prefs: Prefs | None = None,
    fetchers: Sequence[Fetcher] | None = None,
    db_path: Path | None = None,
    dry_run: bool = False,
) -> RunStats:
    if prefs is not None or fetchers is not None:
        return _run_search(
            prefs or load_prefs(),
            list(fetchers) if fetchers is not None else None,
            db_path=db_path,
            dry_run=dry_run,
        )
    totals = RunStats(fetched=0, upserted=0, new_notified=0, drop_notified=0)
    for search in load_searches():
        part = _run_search(search, None, db_path=db_path, dry_run=dry_run)
        totals = RunStats(
            fetched=totals.fetched + part.fetched,
            upserted=totals.upserted + part.upserted,
            new_notified=totals.new_notified + part.new_notified,
            drop_notified=totals.drop_notified + part.drop_notified,
        )
    log.info(
        "all searches fetched=%s upserted=%s new_notified=%s drop_notified=%s",
        totals.fetched,
        totals.upserted,
        totals.new_notified,
        totals.drop_notified,
    )
    return totals


def _run_search(
    prefs: Prefs,
    fetchers: Sequence[Fetcher] | None,
    *,
    db_path: Path | None,
    dry_run: bool,
) -> RunStats:
    fetchers = list(fetchers) if fetchers is not None else _available_fetchers(prefs)
    log.info(
        "search=%s adapters: %s",
        prefs.listing_source or prefs.operation,
        ", ".join(prefs.enabled_adapters) or "(ninguno)",
    )

    listings: list[Listing] = []
    for fetch in fetchers:
        name = getattr(fetch, "__module__", fetch.__name__)
        try:
            listings.extend(fetch(prefs))
        except Exception as exc:
            log.warning("adapter %s failed: %s", name, exc)
            if "meli" in name.lower():
                log.warning("MELI HTML: si es WAF/captcha no se burla; robots no permite _Desde_")
            print(f"WARN {name}: {exc}")

    fetched = len(listings)
    upserted = 0
    new_notified = 0
    drop_notified = 0

    for listing in listings:
        if not is_watch(listing, prefs):
            continue

        result = upsert_listing(listing, db_path=db_path)
        upserted += 1

        if _should_notify_new(result, listing, prefs, db_path):
            if not dry_run:
                send_new(listing)
                record_notification(
                    "new",
                    listing.source,
                    listing.external_id,
                    new_usd=listing.price_usd,
                    db_path=db_path,
                )
            new_notified += 1

        new_usd = result.new_price_usd
        old_usd = result.old_price_usd
        if (
            result.price_changed
            and new_usd is not None
            and is_drop(old_usd, new_usd, prefs)
            and not already_notified_drop(
                listing.source, listing.external_id, new_usd, db_path=db_path
            )
        ):
            if not dry_run:
                assert old_usd is not None
                send_price_drop(listing, old_usd, new_usd)
                record_notification(
                    "price_drop",
                    listing.source,
                    listing.external_id,
                    old_usd=old_usd,
                    new_usd=new_usd,
                    db_path=db_path,
                )
            drop_notified += 1

    stats = RunStats(
        fetched=fetched,
        upserted=upserted,
        new_notified=new_notified,
        drop_notified=drop_notified,
    )
    log.info(
        "search=%s fetched=%s upserted=%s new_notified=%s drop_notified=%s",
        prefs.listing_source or prefs.operation,
        stats.fetched,
        stats.upserted,
        stats.new_notified,
        stats.drop_notified,
    )
    return stats


def _should_notify_new(
    result: UpsertResult,
    listing: Listing,
    prefs: Prefs,
    db_path: Path | None,
) -> bool:
    """NUEVA si califica y nunca se avisó. Un dry-run no debe silenciar el primer envío.

    Si el precio cambió, el caso es BAJÓ (entrar al rango), no un extra NUEVA.
    """
    if not is_match(listing, prefs):
        return False
    if already_notified_new(listing.source, listing.external_id, db_path=db_path):
        return False
    if not result.created and result.price_changed:
        return False
    return True


def main(argv: list[str] | None = None) -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch y upsert; no envía Telegram ni registra notificaciones",
    )
    args = parser.parse_args(argv)
    if not args.dry_run:
        try:
            require_credentials()
        except TelegramConfigError as exc:
            raise SystemExit(f"{exc}. Copiá .env.example a .env y completá Telegram.") from exc
    stats = run(dry_run=args.dry_run)
    print(
        f"fetched={stats.fetched} upserted={stats.upserted} "
        f"new_notified={stats.new_notified} drop_notified={stats.drop_notified}"
    )


if __name__ == "__main__":
    main()
