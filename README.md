# rental-scout

Buscador de alquileres en General Pacheco (Tigre, GBA Norte). Corre por cron, persiste avisos y avisa por Telegram.

Preferencias en YAML, modelo `Listing` y SQLite (`data/scout.db`). Todavía no hay adapters, matcher ni Telegram.

## Requisitos

- Python 3.12+

## Setup

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Copiá `.env.example` a `.env` cuando haga falta (Telegram todavía no está cableado):

```bash
cp .env.example .env
```

## Correr el stub

Desde la raíz del repo, con el venv activo:

```bash
python -m jobs.run_once
```

Debería imprimir `dry-run: 0 listings` y salir con código 0.

## Tests

```bash
pytest
```
