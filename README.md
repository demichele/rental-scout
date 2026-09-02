# rental-scout

Buscador de alquileres en General Pacheco (Tigre, GBA Norte). Corre por cron, persiste avisos y avisa por Telegram.

Preferencias en YAML, modelo `Listing`, SQLite (`data/scout.db`) y avisos Telegram (`NUEVA` / `BAJÓ DE PRECIO`). Portal activo: **ZonaProp**. Mercado Libre y Argenprop quedan apagados (403); se reactivan en `prefs.yaml` → `enabled_adapters`.

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
- `MELI_ACCESS_TOKEN`: opcional por ahora (el adapter MELI está apagado)

ZonaProp es HTML público. Si responde captcha/WAF (403), se salta ese portal; no se burla.

## Correr una pasada

Desde la raíz del repo, con el venv activo (carga `.env` de acá):

```bash
python -m jobs.ping_telegram
python -m jobs.run_once --dry-run
python -m jobs.run_once
```

`--dry-run` trae ZonaProp y persiste watch, **sin** Telegram. La pasada real manda `NUEVA` / `BAJÓ DE PRECIO` a los avisos en 1500–2500 USD (y no se silencia si antes corriste dry-run).

## Servicio cada 30 minutos (macOS)

LaunchAgent de usuario: corre `jobs.run_once` al cargar y después cada 30 min. Usa el `venv` del repo y el `.env` (Telegram).

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

No hace falta para el flujo actual. Cuando reactivemos MELI:

```bash
python -m jobs.ping_meli
```

Si el token está vencido (HTTP 403), autorizá de nuevo (ver [`oauth/README.md`](oauth/README.md)) y actualizá `MELI_ACCESS_TOKEN`.

## Tests

```bash
pytest
```

Los tests de Telegram mockean `httpx` y no pegan al bot de verdad.
