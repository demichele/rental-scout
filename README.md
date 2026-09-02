# rental-scout

Buscador de alquileres en General Pacheco (Tigre, GBA Norte). Corre por cron, persiste avisos y avisa por Telegram.

Preferencias en YAML, modelo `Listing`, SQLite (`data/scout.db`) y avisos Telegram (`NUEVA` / `BAJÓ DE PRECIO`). Todavía no hay adapters ni matcher.

## Requisitos

- Python 3.12+

## Setup

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Copiá `.env.example` a `.env` y completá las credenciales de Telegram:

```bash
cp .env.example .env
```

- `TELEGRAM_BOT_TOKEN`: token del bot (BotFather)
- `TELEGRAM_CHAT_ID`: id del chat (usuario o grupo) donde van los avisos

## Correr el stub

Desde la raíz del repo, con el venv activo:

```bash
python -m jobs.run_once
```

Debería imprimir `dry-run: 0 listings` y salir con código 0.

## Ping de Telegram

Con el venv activo y `.env` completo, desde la raíz del repo:

```bash
python -m jobs.ping_telegram
```

Manda un aviso **NUEVA** y uno **BAJÓ DE PRECIO** con listings fake (no toca la DB). El de baja usa 3000 → 2200 USD para mostrar `Entró al rango 1500–2500 USD`. Si faltan las variables, sale error y no pega a la API.

## Tests

```bash
pytest
```

Los tests de Telegram mockean `httpx` y no pegan al bot de verdad.
