# rental-scout

Buscador de alquileres en General Pacheco (Tigre, GBA Norte). Corre por cron, persiste avisos y avisa por Telegram.

Preferencias en YAML, modelo `Listing`, SQLite (`data/scout.db`), adapters (Mercado Libre, ZonaProp, Argenprop) y avisos Telegram (`NUEVA` / `BAJÓ DE PRECIO`).

## Requisitos

- Python 3.12+

## Setup

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

Copiá `.env.example` a `.env` y completá las credenciales:

```bash
cp .env.example .env
```

- `TELEGRAM_BOT_TOKEN`: token del bot (BotFather)
- `TELEGRAM_CHAT_ID`: id del chat (usuario o grupo) donde van los avisos
- `MELI_ACCESS_TOKEN`: Bearer de usuario (`APP_USR-...`), no el Client Secret. En Vercel, Root Directory = `oauth`. Ver [`oauth/README.md`](oauth/README.md).

ZonaProp y Argenprop son HTML: si el sitio redirige, el adapter sigue el 301. Si responde captcha/WAF (403), se salta ese portal; no se burla.

## Correr una pasada

Desde la raíz del repo, con el venv activo (carga `.env` de acá):

```bash
python -m jobs.run_once --dry-run
python -m jobs.run_once
```

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
