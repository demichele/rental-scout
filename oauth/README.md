# OAuth Mercado Libre (redirect URI)

Mini callback en Vercel: Mercado Libre redirige a una URL **fija**, el server intercambia el `code` (vale 10 min, un solo uso) por `access_token` + `refresh_token`.

La doc de [OAuth de Mercado Pago](https://www.mercadopago.com.ar/developers/es/docs/security/oauth) describe el mismo flujo (Authorization Code), pero **rental-scout busca avisos en Mercado Libre**. Creá la app en [developers.mercadolibre.com.ar](https://developers.mercadolibre.com.ar), no en Mercado Pago.

- Autorizar: `https://auth.mercadolibre.com.ar/authorization`
- Canje: `POST https://api.mercadolibre.com/oauth/token`

El `pyproject.toml` del buscador hace que Vercel crea que el repo es Python. Por eso el deploy vive en la **raíz** del repo (`api/`, `lib/`, `public/`, `vercel.json` con `"framework": null`), no en un subdirectorio.

## Redirect URI

Tiene que coincidir **carácter por carácter** en tres lugares:

1. Campo Redirect URI de la app en Mercado Libre
2. Env `MELI_REDIRECT_URI` en Vercel
3. Query `redirect_uri` del authorize y del POST `/oauth/token` (esto lo arma la app)

```text
https://TU-PROYECTO.vercel.app/api/callback
```

Sin slash al final, sin `?`, sin URL de preview.

## Deploy en Vercel

1. Importá el repo `rental-scout`.
2. **Root Directory:** dejalo vacío / `.` (la raíz del repo). Si quedó en `oauth`, cambialo: Settings → General → Root Directory.
3. Framework Preset: Other (el `vercel.json` ya pone `"framework": null` para no usar el builder de Python).
4. Env vars (Production):

   - `MELI_CLIENT_ID` — App ID
   - `MELI_CLIENT_SECRET` — Secret (el string corto de ~32 chars)
   - `MELI_REDIRECT_URI` — `https://TU-PROYECTO.vercel.app/api/callback`

5. Deploy. Pegá esa misma URI en la app de Mercado Libre.
6. Home del dominio → **Autorizar con Mercado Libre** (cuenta dueña, no operador).
7. Copiá `MELI_ACCESS_TOKEN` (`APP_USR-...`) al `.env` local.

El token dura ~6 horas. Guardá también `MELI_REFRESH_TOKEN`.
