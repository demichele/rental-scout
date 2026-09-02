"""Valida el token OAuth de Mercado Libre.

No corre el scout: sin DB, sin Telegram, sin paginar listings.
Usalo antes de `jobs.run_once` o cuando el adapter MELI loguee 401/403.
"""

from pathlib import Path

from dotenv import load_dotenv

from src.adapters.meli import check_connection

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    load_dotenv(PROJECT_ROOT / ".env")
    result = check_connection()
    print(result.message)
    if result.user_id or result.nickname:
        print(f"user_id={result.user_id or '-'} nickname={result.nickname or '-'}")
    if result.search_http is not None:
        total = result.search_total if result.search_total is not None else "-"
        print(f"search HTTP {result.search_http} total={total}")
    if not result.ok:
        raise SystemExit(1)
    print("ok: conexión MELI lista para jobs.run_once")


if __name__ == "__main__":
    main()
