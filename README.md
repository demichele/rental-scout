# rental-scout

Buscador de alquileres en countries de Tigre / Pacheco. Corre por cron, persiste avisos y avisa por Telegram.

Preferencias en YAML, modelo `Listing`, SQLite (`data/scout.db`) y avisos Telegram (`NUEVA` / `BAJÓ DE PRECIO`). Portal activo: **ZonaProp**. Barrios: Talar del Lago, Nordelta, Los Alisos, La Comarca, Barrancas de Santa Maria, Barrancas de San Jose, Santa Barbara. Tope **2500 USD**. Argenprop y Mercado Libre quedan apagados.

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
- `MELI_ACCESS_TOKEN`: opcional para el scout (busca HTML). Lo usa `ping_meli` para la API.
- `MELI_REFRESH_TOKEN`: copialo de la página OAuth si regenerás el token. Hoy el scout no lo usa.

`MELI_CLIENT_ID`, `MELI_CLIENT_SECRET` y `MELI_REDIRECT_URI` **no** van en este `.env`: son env de Vercel (`oauth/`). Ver [`oauth/README.md`](oauth/README.md).

ZonaProp es HTML público (SERPs por barrio). Si responde captcha/WAF (403), se salta esa SERP; no se burla. Argenprop y MELI están apagados en `prefs.yaml`.

## Correr una pasada

Desde la raíz del repo, con el venv activo (carga `.env` de acá):

```bash
python -m jobs.ping_meli
python -m jobs.ping_telegram
python -m jobs.run_once --dry-run
python -m jobs.run_once
```

`--dry-run` trae ZonaProp y persiste watch, **sin** Telegram. La pasada real manda `NUEVA` / `BAJÓ DE PRECIO` a los avisos en 1500–2500 USD (y no se silencia si antes corriste dry-run).

## Servicio cada 30 minutos (macOS)

LaunchAgent de usuario: corre `jobs.run_once` al cargar y después cada 30 min. Usa el `venv` del repo y el `.env` (Telegram). Solo ZonaProp.

```bash
chmod +x jobs/install_launchd.sh jobs/uninstall_launchd.sh jobs/run_scheduled.sh
./jobs/install_launchd.sh
```

Logs en `~/Library/Logs/rental-scout/`. Para ver si está cargado:

```bash
launchctl print gui/$(id -u)/com.rentalscout.run | head
```

Si el primer run falla con `Operation not permitted`, el repo está en Documentos y macOS bloquea a launchd. En **Ajustes → Privacidad y seguridad → Acceso completo al disco** agregá `venv/bin/python` (el binario del venv del repo).

Para pararlo:

```bash
./jobs/uninstall_launchd.sh
```

## Ping de Telegram

Con el venv activo y `.env` completo, desde la raíz del repo:

```bash
python -m jobs.ping_telegram
```

Manda un aviso **NUEVA** y uno **BAJÓ DE PRECIO** con listings fake (no toca la DB). El de baja usa 3000 → 2200 USD para mostrar `Entró al rango 1500–2500 USD`. Si faltan las variables, sale error y no pega a la API.

## Ping de Mercado Libre

Validador de token, separado del scout:

```bash
python -m jobs.ping_meli
```

Hace `GET /users/me` y un search API `limit=1`. No pagina, no escribe DB, no manda Telegram. **El scout no usa este search**: busca la SERP HTML de inmuebles.

Si `/users/me` da 200 y el search API da 403, es esperado. Si `/users/me` también falla, renovás `MELI_ACCESS_TOKEN` (ver [`oauth/README.md`](oauth/README.md)).

## Tests

```bash
pytest
```

Los tests de Telegram mockean `httpx` y no pegan al bot de verdad.
